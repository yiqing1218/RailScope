"""Exercise the actual bridge queue and localhost handlers, not just construction."""
from http.server import ThreadingHTTPServer
import json
from types import SimpleNamespace
from threading import Thread
from urllib.request import urlopen, Request


def test_ready_bridge_dispatches_visibility_and_focus():
    from launcher import MapView
    from map_commands import MapCommands
    calls = []
    view = SimpleNamespace(is_ready=False, command_in_flight=False, commands=MapCommands())
    view.page = lambda: SimpleNamespace(runJavaScript=lambda code, callback: (calls.append(code), callback(None)))
    view._dispatch = lambda: MapView._dispatch(view)
    view._completed = lambda result: MapView._completed(view, result)
    MapView.call(view, "setVisibility", "metro", True)
    MapView.call(view, "focus", [121, 31], "站点")
    assert calls == []
    MapView._ready(view)
    assert len(calls) == 2
    assert 'setVisibility("metro",true)' in calls[0]
    assert 'focus([121, 31],"站点")' in calls[1]
    assert not view.command_in_flight


def test_metro_viewport_is_served_over_http(monkeypatch):
    import launcher
    calls = []
    def query(*args):
        calls.append(args)
        return {"type": "FeatureCollection", "features": [{"id": 7}]}
    monkeypatch.setattr(launcher, "metro_viewport", query)
    server = ThreadingHTTPServer(("127.0.0.1", 0), launcher.LocalHandler)
    server.metro_db = "test.sqlite"
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/metro?kind=metro&bbox=120,30,122,32&selected=%5B11%5D", timeout=3) as response:
            assert response.status == 200
            assert json.load(response)["features"] == [{"id": 7}]
        assert calls == [("test.sqlite", "metro", [120, 30, 122, 32], [11])]
        # A national station selection exceeds the 64 KiB HTTP request-line
        # limit. Exercise the same POST transport used by the real map.
        selected = ['station-' + str(i) for i in range(16000)]
        body = json.dumps({'kind':'stations','bbox':'120,30,122,32','selected':json.dumps(selected)}).encode()
        assert len(body) > 65536
        request = Request(f"http://127.0.0.1:{server.server_port}/api/metro", data=body,
                          headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=3) as response:
            assert response.status == 200
        assert calls[-1][-1] == selected
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
