"""Generate and audit the four locally supplied, trusted Shanghai schedule packages.

Run only after reviewing the Python generators: loading a generator executes Python.
Original packages and installed/saved plans are never modified by this tool.
"""

import argparse
import ast
import json
import sys
from pathlib import Path
from types import ModuleType


class ReverseTerminalFix(ast.NodeTransformer):
    """Make inclusive reverse slices include index zero, without changing originals."""

    def visit_Slice(self, node):
        self.generic_visit(node)
        step, stop = node.step, node.upper
        if (
            isinstance(step, ast.UnaryOp)
            and isinstance(step.op, ast.USub)
            and isinstance(step.operand, ast.Constant)
            and step.operand.value == 1
            and isinstance(stop, ast.BinOp)
            and isinstance(stop.op, ast.Sub)
            and isinstance(stop.left, ast.Name)
            and isinstance(stop.right, ast.Constant)
            and stop.right.value == 1
        ):
            node.upper = ast.IfExp(
                test=ast.Compare(left=stop.left, ops=[ast.Eq()], comparators=[ast.Constant(0)]),
                body=ast.Constant(None), orelse=stop,
            )
        return node


def load_generator(path):
    tree = ReverseTerminalFix().visit(ast.parse(path.read_text(encoding="utf-8")))
    module = ModuleType(path.stem)
    module.__file__ = str(path)
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), module.__dict__)
    return module


def verify(root, output):
    sys.path.insert(0, str(root / "desktop"))
    from operating import Plan

    packages = [
        ("RailScope_Shanghai_Line1_Reconstruction", "shanghai_line1_railscope_generator.py"),
        ("RailScope_Shanghai_Metro_Lines_2_3_7_8_9", "generate_railscope_metro_2_3_7_8_9.py"),
        ("RailScope_Shanghai_Metro_Lines_4_5_6_11_12", "generate_railscope_metro_4_5_6_11_12.py"),
        ("RailScope_Shanghai_Metro_Lines_10_13_14_15_16_17_18_Pujiang", "generate_railscope_metro_10_13_14_15_16_17_18_pujiang.py"),
    ]
    output.mkdir(parents=True, exist_ok=True)
    modules = [load_generator(root / folder / name) for folder, name in packages]
    lines, active = modules[2].load_lines(root)
    combined = {day: Plan(lines) for day in ("weekday", "weekend")}
    report = []
    for index, module in enumerate(modules):
        spec_file = ["line1_public_reconstruction_spec.json", "metro_2_3_7_8_9_research_spec.json", "metro_4_5_6_11_12_research_spec.json", "metro_10_13_14_15_16_17_18_pujiang_research_spec.json"][index]
        spec = json.loads((root / packages[index][0] / spec_file).read_text(encoding="utf-8"))
        numbers = ["1"] if index == 0 else list(spec["lines"])
        for number in numbers:
            for day in combined:
                entry = {"line": number, "day": day}
                try:
                    if index == 0:
                        line = next(line for line in lines if line["id"] == "sh-1")
                        resolved, direction = module.resolve_station_map(line)
                        payload = module.build_plan("mon-thu" if day == "weekday" else "saturday", line, resolved, direction)
                    elif index == 1:
                        cfg = spec["lines"][number]
                        line = next(line for line in lines if line["id"] == cfg["line_id"])
                        resolved, direction = module.resolve_line(line, cfg)
                        payload = module.build_line_plan(cfg, line, resolved, direction, day)
                    else:
                        builder = getattr(module, "buildP" if number == "浦江" else "build" + number)
                        trains = builder(lines, spec["lines"][number], day)
                        payload = module.payload(trains, [number], day, "保留原生成器重建逻辑；仅适配反向切片终点")
                    target = output / f"line{number}_{day}.json"
                    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
                    plan = Plan(lines)
                    plan.load(target)
                    # Test every trip, not just one representative per line.
                    for train in plan.trains:
                        start = train["stops"][0]["departure_s"]
                        end = train["stops"][1]["arrival_s"]
                        a = plan.position(train["id"], start + (end - start) / 3)
                        b = plan.position(train["id"], start + 2 * (end - start) / 3)
                        if not a or not b or a["distance_m"] == b["distance_m"]:
                            raise ValueError(f"{train['id']}：首区间位置未推进")
                    combined[day].trains.extend(plan.trains)
                    combined[day].extensions[f"user.local/package-{number}"] = plan.extensions
                    entry.update(status="OK", trips=len(plan.trains), path=str(target), motion_tested=len(plan.trains))
                except (Exception, SystemExit) as error:
                    entry.update(status="FAIL", error=str(error))
                report.append(entry)
                print(json.dumps(entry, ensure_ascii=False), flush=True)
    for day, plan in combined.items():
        plan.save(output / f"shanghai_validated_{day}.json")
    audit = {"official": False, "active_dataset": str(active), "results": report,
             "combined_trips": {day: len(plan.trains) for day, plan in combined.items()}}
    (output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    verify(args.root, args.output or args.root / "data/processed/operations/validated_shanghai")
