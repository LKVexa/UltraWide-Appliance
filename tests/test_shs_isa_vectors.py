"""Compact, reusable BASIC-1048576 ISA differential vectors.

No installer is executed. Import vectors(), resolve_value(), and run_reference().
CLI --check validates hand-authored expectations against the recovered reference.
CLI --json exports the compact inputs; --results exports normalized oracle results.
All output goes to stdout; this module does not write results or alter the source.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys

WORD_BITS = 1048576
WORD_BYTES = WORD_BITS // 8
WIDTHS = tuple(1 << n for n in range(3, 21))
MODES = ("wrapping", "checked", "saturating", "trapping")
DEFAULT_SOURCE = None


def resolve_value(value):
    """Parse int, exact hex, mask:N, bit:N, or bits:N,M; never eval input."""
    if isinstance(value, int):
        return value
    if value.startswith("mask:"):
        return (1 << int(value[5:])) - 1
    if value.startswith("bit:"):
        return 1 << int(value[4:])
    if value.startswith("bits:"):
        return sum(1 << int(n) for n in value[5:].split(","))
    return int(value, 0)


def ins(name, **args):
    return {"name": name, **args}


def mov(register, immediate, width=WORD_BITS):
    return ins("MOVI", rd=register, immediate=immediate, width=width)


def flags(z=False, n=False, c=False, v=False):
    return {"Z": z, "N": n, "C": c, "V": v}


def vector(name, code, expected=None, **setup):
    if not code or code[-1]["name"] != "HALT":
        code = [*code, ins("HALT")]
    return {"id": name, "instructions": code, "expected": expected or {}, **setup}


def binary(name, opcode, a, b, result, width=8, mode="wrapping", status=None):
    code = [mov(0, a), mov(1, b), ins(opcode, rd=2, ra=0, rb=1, width=width, mode=mode)]
    expected = {"registers": {"2": result}}
    if status is not None:
        expected["flags"] = status
    return vector(name, code, expected)


def vectors():
    """Return compact JSON-compatible inputs with independent explicit assertions."""
    out = [
        vector("nop-preserves-flags", [mov(0, 128, 8), ins("NOP")], {"flags": flags(n=True)}),
        vector("mov-clears-upper-and-cv", [mov(0, "mask:1048576"), ins("MOV", rd=1, ra=0, width=8)], {"registers": {"1": 255}, "flags": flags(n=True)}),
        vector("mov-overlapping-register", [mov(0, 511), ins("MOV", rd=0, ra=0, width=8)], {"registers": {"0": 255}, "flags": flags(n=True)}),
        binary("add-wrap-byte", "ADD", 255, 1, 0, status=flags(z=True, c=True, v=True)),
        binary("subtract-underflow-byte", "SUB", 0, 1, 255, status=flags(n=True, c=True, v=True)),
        binary("multiply-wrap-byte", "MUL", 128, 2, 0, status=flags(z=True, c=True, v=True)),
        binary("unsigned-division-byte", "DIVU", 255, 2, 127, status=flags()),
        binary("unsigned-remainder-byte", "MODU", 255, 2, 1, status=flags()),
        binary("and-byte", "AND", 170, 204, 136, status=flags(n=True)),
        binary("or-byte", "OR", 170, 204, 238, status=flags(n=True)),
        binary("xor-byte", "XOR", 170, 204, 102, status=flags()),
        vector("not-masked-byte", [mov(0, 256), ins("NOT", rd=2, ra=0, width=8)], {"registers": {"2": 255}, "flags": flags(n=True)}),
        binary("shift-left-byte-overflow", "SHL", 128, 1, 0, status=flags(z=True, c=True, v=True)),
        binary("shift-right-byte", "SHR", 128, 1, 64, status=flags()),
        binary("shift-count-is-lane-masked-then-modulo", "SHL", 3, 265, 6, status=flags()),
        binary("shift-count-equal-width-is-zero", "SHR", 128, 8, 128, status=flags(n=True)),
        binary("arithmetic-operands-masked", "ADD", 511, 257, 0, status=flags(z=True, c=True, v=True)),
        vector("add-destination-alias", [mov(0, 255), mov(1, 1), ins("ADD", rd=0, ra=0, rb=1, width=8)], {"registers": {"0": 0}, "flags": flags(z=True, c=True, v=True)}),
        vector("cmp-equal-carry-set", [mov(0, 255), mov(1, 511), ins("CMP", ra=0, rb=1, width=8)], {"flags": flags(z=True, c=True)}),
        vector("cmp-unsigned-less-negative-set", [mov(0, 127), mov(1, 255), ins("CMP", ra=0, rb=1, width=8)], {"flags": flags(n=True)}),
        vector("cmp-unsigned-greater-negative-clear", [mov(0, 255), mov(1, 127), ins("CMP", ra=0, rb=1, width=8)], {"flags": flags(c=True)}),
        vector("jmp-absolute-instruction-index", [ins("JMP", immediate=2), mov(0, 7), mov(1, 9)], {"registers": {"0": 0, "1": 9}, "steps": 3}),
        vector("jz-taken", [mov(0, 0), ins("JZ", immediate=3), mov(1, 7)], {"registers": {"1": 0}, "flags": flags(z=True), "steps": 3}),
        vector("jz-not-taken", [mov(0, 1), ins("JZ", immediate=3), mov(1, 7)], {"registers": {"1": 7}, "steps": 4}),
        vector("jnz-taken", [mov(0, 1), ins("JNZ", immediate=3), mov(1, 7)], {"registers": {"1": 0}, "steps": 3}),
        vector("jnz-not-taken", [mov(0, 0), ins("JNZ", immediate=3), mov(1, 7)], {"registers": {"1": 7}, "steps": 4}),
        vector("halt-masks-exit-to-32-bits", [ins("HALT", immediate="0x100000007")], {"exit_code": 7, "flags": flags()}),
        vector("store-little-endian-unaligned", [mov(0, "0x12345678"), ins("STORE", rd=0, rb=0, immediate=3, width=32), ins("LOAD", rd=1, rb=0, immediate=4, width=16)], {"registers": {"1": "0x3456"}, "memory_hex": {"3": "78563412"}, "flags": flags()}),
        vector("store-preserves-flags", [mov(0, 128, 8), ins("STORE", rd=0, rb=0, immediate=0, width=8)], {"flags": flags(n=True), "memory_hex": {"0": "80"}}),
        vector("load-clears-upper-and-cv", [mov(0, 255), mov(1, 1), ins("ADD", rd=2, ra=0, rb=1, width=8), ins("LOAD", rd=0, rb=0, immediate=0, width=8)], {"registers": {"0": 0}, "flags": flags(z=True)}),
        vector("capability-relative-offset", [mov(0, 123), ins("STORE", rd=0, rb=1, immediate=2, width=8), ins("LOAD", rd=1, rb=0, immediate=18, width=8)], {"registers": {"1": 123}, "memory_hex": {"18": "7b"}}, cap_table={"1": {"base": 16, "length": 8, "permissions": ["read", "write"]}}),
        vector("full-word-stack-lifo-ignores-width", [mov(0, "bit:1048575"), ins("PUSH", ra=0, width=8), mov(0, 7), ins("PUSH", ra=0, width=8), mov(2, 0), ins("POP", rd=1, width=8), ins("POP", rd=2, width=8)], {"registers": {"1": 7, "2": "bit:1048575"}, "sp": 3 * WORD_BYTES, "flags": flags(z=True)}),
        vector("svc-unsigned", [mov(0, 123), ins("SVC", ra=0, immediate=1)], {"stdout": "123"}),
        vector("svc-signed-full-word", [mov(0, "mask:1048576"), ins("SVC", ra=0, immediate=2, width=8)], {"stdout": "-1"}),
        vector("svc-character", [mov(0, 0x1F642), ins("SVC", ra=0, immediate=4)], {"stdout": "\U0001f642"}),
        vector("svc-full-hex-width", [mov(0, 1), ins("SVC", ra=0, immediate=3, width=8)], {"stdout_length": 2 + WORD_BYTES * 2, "stdout_suffix": "00000001"}),
        vector("svc-evidence-mark", [mov(0, "bit:1048575"), ins("SVC", ra=0, immediate=5)], {"evidence_values": ["bit:1048575"]}),
        vector("svc-no-implicit-capability-check", [mov(0, 65), ins("SVC", ra=0, immediate=4)], {"stdout": "A"}),
        vector("arithmetic-zero-replaces-cmp-for-branch", [mov(0, 1), mov(1, 2), ins("CMP", ra=0, rb=1, width=8), ins("SUB", rd=2, ra=0, rb=0, width=8), ins("JZ", immediate=6), mov(3, 9)], {"registers": {"2": 0, "3": 0}, "flags": flags(z=True), "steps": 6}),
    ]
    for mode in MODES:
        out.append(vector(f"movi-lane-mask-ignores-{mode}", [ins("MOVI", rd=0, immediate=511, width=8, mode=mode)], {"registers": {"0": 255}, "flags": flags(n=True)}))
    # The narrow-lane behavior and highest-bit handling are tested for every ISA width.
    for width in WIDTHS:
        out.append(vector(f"movi-lane-mask-{width}", [mov(0, "mask:1048576", width)], {"registers": {"0": f"mask:{width}"}, "flags": flags(n=True)}))
        out.append(binary(f"add-boundary-{width}", "ADD", f"mask:{width}", 1, 0, width, status=flags(z=True, c=True, v=True)))
        out.append(binary(f"right-shift-top-bit-{width}", "SHR", f"bit:{width-1}", width - 1, 1, width, status=flags()))
        out.append(binary(f"shift-modulo-{width}", "SHL", 1, width + 1, 2, width, status=flags()))
        out.append(vector(f"memory-width-{width}", [mov(0, f"bits:{width-1},0"), ins("STORE", rd=0, rb=0, immediate=3, width=width), ins("LOAD", rd=1, rb=0, immediate=3, width=width)], {"registers": {"1": f"bits:{width-1},0"}, "flags": flags(n=True)}))
    # Arithmetic modes must agree both when no overflow occurs and at either boundary.
    for width in (8, WORD_BITS):
        for mode in MODES:
            out.append(binary(f"mode-success-{width}-{mode}", "ADD", 2, 3, 5, width, mode, flags()))
            for opcode, a, b, sat in (("ADD", f"mask:{width}", 1, f"mask:{width}"), ("SUB", 0, 1, 0), ("MUL", f"bit:{width-1}", 2, f"mask:{width}"), ("SHL", f"bit:{width-1}", 1, f"mask:{width}")):
                name = f"mode-overflow-{opcode.lower()}-{width}-{mode}"
                if mode in ("checked", "trapping"):
                    expected = {"fault": f"B1048576-ARITH-{mode.upper()}: result outside unsigned {width}-bit range", "registers": {"2": 0}}
                    out.append(vector(name, [mov(0, a), mov(1, b), ins(opcode, rd=2, ra=0, rb=1, width=width, mode=mode)], expected))
                elif mode == "saturating":
                    out.append(binary(name, opcode, a, b, sat, width, mode, flags(z=sat == 0, n=sat != 0, c=True, v=True)))
    out += [
        binary("full-width-bitwise-xor", "XOR", "bits:1048575,63,0", "bits:63,1", "bits:1048575,1,0", WORD_BITS, status=flags(n=True)),
        binary("full-width-division-power-two", "DIVU", "bit:1048575", 2, "bit:1048574", WORD_BITS, status=flags()),
        binary("full-width-remainder-power-two", "MODU", "bits:1048575,0", 2, 1, WORD_BITS, status=flags()),
        vector("full-width-not-zero", [ins("NOT", rd=0, ra=1)], {"registers": {"0": "mask:1048576"}, "flags": flags(n=True)}),
        vector("fault-divide-zero", [mov(0, 7), ins("DIVU", rd=2, ra=0, rb=1)], {"fault": "B1048576-ARITH: division by zero", "registers": {"2": 0}}),
        vector("fault-modulo-zero", [mov(0, 7), ins("MODU", rd=2, ra=0, rb=1)], {"fault": "B1048576-ARITH: division by zero"}),
        vector("fault-divisor-masked-to-zero", [mov(0, 7), mov(1, 256), ins("DIVU", rd=2, ra=0, rb=1, width=8)], {"fault": "B1048576-ARITH: division by zero"}),
        vector("fault-invalid-capability", [ins("LOAD", rd=0, rb=15, width=8)], {"fault": "B1048576-CAP: invalid capability C15"}),
        vector("fault-read-permission", [ins("LOAD", rd=0, rb=1, width=8)], {"fault": "B1048576-CAP: missing read permission"}, cap_table={"1": {"base": 0, "length": 8, "permissions": ["write"]}}),
        vector("fault-write-permission", [ins("STORE", rd=0, rb=1, width=8)], {"fault": "B1048576-CAP: missing write permission"}, cap_table={"1": {"base": 0, "length": 8, "permissions": ["read"]}}),
        vector("fault-revoked-capability", [ins("LOAD", rd=0, rb=1, width=8)], {"fault": "B1048576-CAP: capability revoked"}, cap_table={"1": {"base": 0, "length": 8, "permissions": ["read"], "revoked": True}}),
        vector("fault-bounds-one-byte", [ins("LOAD", rd=0, rb=0, immediate=3 * WORD_BYTES, width=8)], {"fault": "B1048576-CAP: bounds violation"}),
        vector("fault-full-width-cross-boundary", [ins("STORE", rd=0, rb=0, immediate=2 * WORD_BYTES + 1)], {"fault": "B1048576-CAP: bounds violation"}),
        vector("capability-exact-boundary", [ins("STORE", rd=0, rb=0, immediate=3 * WORD_BYTES - 1, width=8)], {"memory_hex": {str(3 * WORD_BYTES - 1): "00"}}),
        vector("fault-stack-underflow", [ins("POP", rd=0)], {"fault": "B1048576-STACK: underflow"}),
        vector("fault-stack-overflow", [ins("PUSH", ra=0), ins("PUSH", ra=0)], {"fault": "B1048576-STACK: overflow", "sp": 0}, memory_bytes=WORD_BYTES),
        vector("fault-program-counter", [ins("JMP", immediate=99)], {"fault": "B1048576-PC: program counter outside code", "pc": 99}),
        vector("fault-step-limit", [ins("JMP", immediate=0)], {"fault": "B1048576-LIMIT: instruction limit exceeded", "steps": 3}, max_steps=3),
        vector("fault-unknown-service", [ins("SVC", immediate=99)], {"fault": "B1048576-SVC: unsupported service 99"}),
        vector("fault-character-out-of-range", [mov(0, 0x110000), ins("SVC", immediate=4)], {"fault": "B1048576-SVC: invalid Unicode scalar"}),
        vector("fault-missing-declared-capability", [ins("HALT")], {"fault": "B1048576-CAP: missing capabilities: console.write"}, required_capabilities=["console.write"]),
        vector("declared-capability-granted", [ins("HALT")], {}, required_capabilities=["console.write"], granted_capabilities=["console.write"]),
    ]
    assert len({v["id"] for v in out}) == len(out)
    return out


def word_hash(value):
    return hashlib.sha256(resolve_value(value).to_bytes(WORD_BYTES, "little")).hexdigest()


def normalized_state(vm, fault=None):
    """Stable architectural comparison: no Python implementation trace dependency."""
    state = vm.state
    stdout = "".join(vm.stdout)
    return {
        "fault": fault,
        "register_sha256": [word_hash(v) for v in state.registers],
        "memory_sha256": hashlib.sha256(vm.memory).hexdigest(),
        "pc": state.pc, "sp": state.sp, "fp": state.fp,
        "flags": flags(state.zero, state.negative, state.carry, state.overflow),
        "halted": state.halted, "exit_code": state.exit_code,
        "steps": len(vm.trace),
        "stdout": stdout, "evidence_marks": vm.evidence_marks,
    }


def run_reference(item, source=DEFAULT_SOURCE, check=True, reference_module=None):
    """Return exact observable state; assert independent expected fields by default."""
    if reference_module is None:
        packaged_source = str(Path(__file__).resolve().parents[1] / 'src')
        if packaged_source not in sys.path:
            sys.path.insert(0, packaged_source)
        ref = importlib.import_module('ultrawide.shs.reference')
    else:
        ref = reference_module
    vm = ref.Basic1048576VM(item.get("memory_bytes", 3 * WORD_BYTES), item.get("granted_capabilities", []))
    for idx, entry in item.get("cap_table", {}).items():
        vm.cap_table[int(idx)] = ref.Capability(entry["base"], entry["length"], frozenset(entry["permissions"]), revoked=entry.get("revoked", False))
    code = []
    for raw in item["instructions"]:
        instr = dict(raw)
        if "immediate" in instr:
            instr["immediate"] = resolve_value(instr["immediate"])
        code.append(ref.Instruction(**instr))
    blob = ref.build_executable(ref.assemble(code), capabilities=item.get("required_capabilities", []))
    fault = None
    try:
        vm.run(blob, max_steps=item.get("max_steps", 32))
    except ref.VMFault as exc:
        fault = str(exc)
    result = normalized_state(vm, fault)
    if check:
        expected = item["expected"]
        assert fault == expected.get("fault"), (item["id"], "fault", fault, expected.get("fault"))
        for key, value in expected.items():
            if key == "registers":
                for index, wanted in value.items():
                    assert vm.state.registers[int(index)] == resolve_value(wanted), (item["id"], "register", index)
            elif key == "memory_hex":
                for start, wanted in value.items():
                    pos = int(start)
                    assert bytes(vm.memory[pos:pos + len(wanted) // 2]).hex() == wanted, (item["id"], "memory", start)
            elif key == "evidence_values":
                assert result["evidence_marks"] == [word_hash(v) for v in value], item["id"]
            elif key == "stdout_length":
                assert len(result["stdout"]) == value, item["id"]
            elif key == "stdout_suffix":
                assert result["stdout"].endswith(value), item["id"]
            else:
                assert result[key] == value, (item["id"], key, result[key], value)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--json", action="store_true", help="emit compact vector definitions")
    parser.add_argument("--results", action="store_true", help="emit normalized reference results, JSON lines")
    parser.add_argument("--check", action="store_true", help="check all explicit assertions")
    parser.add_argument("--id", help="select a vector by its exact id")
    args = parser.parse_args()
    selected = [v for v in vectors() if not args.id or v["id"] == args.id]
    if not selected:
        parser.error("no matching vector")
    if args.json:
        print(json.dumps({"schema": "basic1048576-isa-vectors-1", "word_bits": WORD_BITS, "vectors": selected}, ensure_ascii=True, indent=2))
        return
    for item in selected:
        result = run_reference(item, args.source)
        if args.results:
            print(json.dumps({"id": item["id"], "result": result}, ensure_ascii=True, separators=(",", ":")))
    if not args.results:
        print(json.dumps({"vectors_passed": len(selected), "opcodes_covered": sorted({i["name"] for v in selected for i in v["instructions"]}), "widths_covered": sorted({i.get("width", WORD_BITS) for v in selected for i in v["instructions"]}), "modes_covered": sorted({i.get("mode", "wrapping") for v in selected for i in v["instructions"]})}))


if __name__ == "__main__":
    main()
