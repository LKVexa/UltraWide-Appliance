"""Independent vector expectations plus full instruction translation equivalence.

Run with unittest discovery from any working directory. These tests use the
preserved reference interpreter as oracle; they do not claim a second VM port.
"""
import sys
import hashlib
import json
import struct
from pathlib import Path
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'src'
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from ultrawide.shs import engine, reference
import test_shs_isa_vectors as isa_vectors


def instructions(item):
    result = []
    for raw in item["instructions"]:
        args = dict(raw)
        if "immediate" in args:
            args["immediate"] = isa_vectors.resolve_value(args["immediate"])
        result.append(reference.Instruction(**args))
    return result


def compile_vector(item):
    program = engine.from_instructions(instructions(item), capabilities=item.get("required_capabilities", ()))
    text = engine.emit(program)
    reparsed = engine.parse(text)
    return text, engine.compile_program(reparsed)


def translated_result(item, blob):
    vm = engine.BoundedVM(memory_bytes=item.get("memory_bytes", 3 * isa_vectors.WORD_BYTES), capabilities=item.get("granted_capabilities", []))
    for idx, cap in item.get("cap_table", {}).items():
        vm.cap_table[int(idx)] = reference.Capability(cap["base"], cap["length"], frozenset(cap["permissions"]), revoked=cap.get("revoked", False))
    fault = None
    try:
        vm.run(blob, max_steps=item.get("max_steps", 32))
    except reference.VMFault as exc:
        fault = str(exc)
    return isa_vectors.normalized_state(vm, fault)


class TranslationVectorTests(unittest.TestCase):
    def test_coverage(self):
        items = isa_vectors.vectors()
        self.assertEqual({i["name"] for v in items for i in v["instructions"]}, set(reference.OPCODES))
        self.assertEqual({i.get("width", reference.WORD_BITS) for v in items for i in v["instructions"]}, set(reference.WIDTHS))
        self.assertEqual({i.get("mode", "wrapping") for v in items for i in v["instructions"]}, set(reference.MODES))

    def test_explicit_oracle_expectations(self):
        for item in isa_vectors.vectors():
            with self.subTest(vector=item["id"]):
                isa_vectors.run_reference(item, reference_module=reference)

    def test_exact_instruction_encoding_round_trip(self):
        for item in isa_vectors.vectors():
            with self.subTest(vector=item["id"]):
                try:
                    _, blob = compile_vector(item)
                except ValueError:
                    # Rejecting invalid branch targets at compile-time is stronger
                    # than allowing the original runtime's out-of-range-PC fault.
                    if item["id"] == "fault-program-counter":
                        continue
                    raise
                verified = reference.verify_executable(blob)
                self.assertEqual(verified["code"], reference.assemble(instructions(item)))
                self.assertEqual(verified["header"]["entry_instruction"], 0)
                self.assertEqual(verified["header"]["capabilities"], sorted(item.get("required_capabilities", [])))

    def test_all_architectural_state_matches(self):
        for item in isa_vectors.vectors():
            with self.subTest(vector=item["id"]):
                oracle = isa_vectors.run_reference(item, reference_module=reference)
                try:
                    _, blob = compile_vector(item)
                except ValueError:
                    if item["id"] == "fault-program-counter":
                        continue
                    raise
                self.assertEqual(translated_result(item, blob), oracle)

    def test_nonzero_entry_is_preserved(self):
        original = [reference.Instruction("MOVI", rd=0, immediate=99), reference.Instruction("HALT", immediate=7)]
        p = engine.from_instructions(original, entry=1)
        blob = engine.compile_program(engine.parse(engine.emit(p)))
        verified = reference.verify_executable(blob)
        self.assertEqual(verified["header"]["entry_instruction"], 1)
        vm = engine.BoundedVM(memory_bytes=isa_vectors.WORD_BYTES)
        result = vm.run(blob, max_steps=2)
        self.assertEqual(vm.state.registers[0], 0)
        self.assertEqual(result["exit_code"], 7)

    def test_noncanonical_header_bytes_round_trip(self):
        program = engine.from_instructions([reference.Instruction("HALT")], debug={"example": "preserve whitespace and key order"})
        header = json.loads(program.header_bytes)
        pretty = (" \n" + json.dumps(dict(reversed(list(header.items()))), indent=3) + "\n ").encode("utf-8")
        original = engine.compile_program(engine.Program(program.instructions, pretty))
        restored = engine.compile_program(engine.parse(engine.emit(engine.from_executable(original))))
        self.assertEqual(restored, original)


class RejectionTests(unittest.TestCase):
    def test_invalid_instructions_fail_before_execution(self):
        cases = [
            reference.Instruction("UNKNOWN"),
            reference.Instruction("MOVI", rd=16),
            reference.Instruction("MOV", ra=-1),
            reference.Instruction("ADD", rb=16),
            reference.Instruction("HALT", width=7),
            reference.Instruction("HALT", mode="unknown"),
            reference.Instruction("MOVI", immediate=-1),
            reference.Instruction("MOVI", immediate=1 << reference.WORD_BITS),
        ]
        for bad in cases:
            with self.subTest(instruction=bad.name, width=bad.width, mode=bad.mode, rd=bad.rd, ra=bad.ra, rb=bad.rb):
                with self.assertRaises((ValueError, reference.Basic1048576Error)):
                    engine.compile_program(engine.from_instructions([bad]))

    def test_empty_program_rejected(self):
        with self.assertRaises(ValueError):
            engine.compile_program(engine.from_instructions([]))

    def test_invalid_entry_rejected(self):
        for entry in (-1, 1):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                engine.compile_program(engine.from_instructions([reference.Instruction("HALT")], entry=entry))

    def test_instruction_limit_rejected(self):
        with self.assertRaises(ValueError):
            engine.compile_program(engine.from_instructions([reference.Instruction("NOP")] * engine.MAX_INSTRUCTIONS + [reference.Instruction("HALT")]))

    def test_unknown_source_rejected(self):
        for source in ("", "{}", "LCTLC/1.0\nHALT", "BRIM", "LCTLC-WIDE/0.1\n"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                engine.parse(source)


class ParserAndBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.program = engine.from_instructions([reference.Instruction("HALT")])
        self.source = engine.emit(self.program)
        self.body = self.source.rsplit("@sha256 ", 1)[0]

    def test_source_tampering_and_trailing_content_rejected(self):
        for text in (self.source.replace("HALT", "NOP", 1), self.source + "trailing\n", self.source.rsplit("@sha256 ", 1)[0]):
            with self.subTest(case=text[-90:]), self.assertRaises((ValueError, reference.Basic1048576Error)):
                engine.parse(text)

    def test_resealed_malformed_rows_and_directives_rejected(self):
        mutations = [
            self.body.replace("I0000|", "I0001|"),
            self.body.replace("|cpu|", "|gpu|"),
            self.body.replace("|HALT|", "|UNKNOWN|"),
            self.body.replace("|R0|R0|R0|", "|R16|R0|R0|"),
            self.body.replace("|R0|R0|R0|", "|R0|R0|R-1|"),
            self.body.replace("|0x0|", "|-1|"),
            self.body.replace("|0x0|", "|0x|"),
            self.body.replace("|0x0|", "|0b0|"),
            self.body.replace("width=1048576", "width=7"),
            self.body.replace("mode=wrapping", "mode=unknown"),
            self.body.replace("mode=wrapping", "mode=wrapping;mode=checked"),
            self.body.replace("@end", "@unknown\n@end"),
            self.body.replace(engine.COLUMNS, engine.COLUMNS + "|EXTRA"),
            self.body.replace("|HALT|", "|HALT|extra|"),
        ]
        for index, text in enumerate(mutations):
            with self.subTest(mutation=index), self.assertRaises((ValueError, reference.Basic1048576Error)):
                engine.parse(engine.seal(text))

    def test_duplicate_header_directive_rejected(self):
        line = next(x for x in self.body.splitlines() if x.startswith("@header "))
        text = self.body.replace(line, line + "\n" + line)
        with self.assertRaises(ValueError):
            engine.parse(engine.seal(text))

    def test_bad_base64_header_rejected(self):
        line = next(x for x in self.body.splitlines() if x.startswith("@header "))
        with self.assertRaises(ValueError):
            engine.parse(engine.seal(self.body.replace(line, "@header !!!!")))

    def test_oversized_source_rejected(self):
        with self.assertRaises(ValueError):
            engine.parse("x" * (engine.MAX_SOURCE_BYTES + 1))

    def test_image_digest_and_reserved_control_bytes_rejected(self):
        blob = engine.compile_program(self.program)
        bad = bytearray(blob)
        bad[-1] ^= 1
        with self.assertRaises((ValueError, reference.Basic1048576Error)):
            engine.from_executable(bytes(bad))
        # Rehash both integrity levels to ensure reserved control bytes are checked.
        header = json.loads(self.program.header_bytes)
        code = bytearray(reference.assemble(self.program.instructions))
        code[6] = 1
        header["code_sha256"] = hashlib.sha256(code).hexdigest()
        hb = reference.canonical_json(header)
        prefix = reference.MAGIC + struct.pack("<I", len(hb)) + hb + code
        with self.assertRaises((ValueError, reference.Basic1048576Error)):
            engine.from_executable(prefix + hashlib.sha256(prefix).digest())

    def test_duplicate_json_header_key_rejected(self):
        hb = self.program.header_bytes
        mutated = b'{"word_bits":8,' + hb[1:]
        with self.assertRaises((ValueError, reference.Basic1048576Error)):
            engine.compile_program(engine.Program(self.program.instructions, mutated))

    def test_unknown_architecture_version_rejected(self):
        header = json.loads(self.program.header_bytes)
        header["architecture_version"] = "999.0.0"
        with self.assertRaises((ValueError, reference.Basic1048576Error)):
            engine.compile_program(engine.Program(self.program.instructions, reference.canonical_json(header)))

    def test_boolean_entry_rejected(self):
        header = json.loads(self.program.header_bytes)
        header["entry_instruction"] = False
        with self.assertRaises((ValueError, reference.Basic1048576Error)):
            engine.compile_program(engine.Program(self.program.instructions, reference.canonical_json(header)))

    def test_wrapper_resource_limits(self):
        for memory in (reference.WORD_BYTES - 1, engine.MAX_MEMORY_BYTES + 1, True):
            with self.subTest(memory=memory), self.assertRaises(ValueError):
                engine.BoundedVM(memory_bytes=memory)
        vm = engine.BoundedVM()
        blob = engine.compile_program(self.program)
        for steps in (0, engine.MAX_STEPS + 1, True):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                vm.run(blob, max_steps=steps)

    def test_wrapper_output_limit(self):
        p = engine.from_instructions([reference.Instruction("MOVI", rd=0, immediate=123), reference.Instruction("SVC", ra=0, immediate=1), reference.Instruction("HALT")])
        with self.assertRaisesRegex(reference.VMFault, "output character limit"):
            engine.BoundedVM(max_output_chars=2).run(engine.compile_program(p))

    def test_wrapper_cancellation(self):
        class Cancelled(Exception):
            pass
        def cancel():
            raise Cancelled()
        with self.assertRaises(Cancelled):
            engine.BoundedVM(cancel=cancel).run(engine.compile_program(self.program))

    def test_decimal_extension_preserves_global_limit(self):
        before = sys.get_int_max_str_digits()
        p = engine.from_instructions([reference.Instruction("MOVI", rd=0, immediate=10**5000), reference.Instruction("SVC", ra=0, immediate=1), reference.Instruction("HALT")])
        result = engine.BoundedVM().run(engine.compile_program(p))
        self.assertEqual(result["stdout"], "1" + "0" * 5000)
        self.assertEqual(sys.get_int_max_str_digits(), before)


if __name__ == "__main__":
    unittest.main()
