"""Airport province/city catalog using the same controls as universities."""
import airport_store
from university_ui import UniversityCatalog

class AirportCatalog(UniversityCatalog):
    profile = airport_store.PROFILE
    store = airport_store
    selection_command = "setAirportSelection"
    reload_command = "reloadAirportViewport"

    def select_airport(self, ident):
        self.select_campus(ident)
