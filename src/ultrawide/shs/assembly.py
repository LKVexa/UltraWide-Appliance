from __future__ import annotations
from pathlib import Path
import re
from .reference import Instruction, assemble, build_executable, Basic1048576Error, MASK, WIDTHS, MODES, WORD_BITS
REG_RE=re.compile(r"^[Rr](1[0-5]|[0-9])$")
CAP_RE=re.compile(r"^[Cc](1[0-5]|[0-9])$")
def reg(t:str)->int:
 m=REG_RE.match(t)
 if not m: raise Basic1048576Error(f"B1048576-ASM: invalid register {t}")
 return int(m.group(1))
def cap(t:str)->int:
 m=CAP_RE.match(t)
 if not m: raise Basic1048576Error(f"B1048576-ASM: invalid capability register {t}")
 return int(m.group(1))
def number(t:str)->int:
 try:v=int(t,0)
 except ValueError as exc: raise Basic1048576Error(f"B1048576-ASM: invalid integer {t}") from exc
 if v<0:v&=MASK
 if not 0<=v<=MASK: raise Basic1048576Error("B1048576-ASM: integer outside 1,048,576-bit range")
 return v
def parse_assembly(source:str):
 lines=[];labels={};caps=set();default_width=WORD_BITS;default_mode="wrapping"
 for raw in source.splitlines():
  text=raw.split(";",1)[0].strip()
  if not text:continue
  low=text.lower()
  if low.startswith(".capability "): caps.add(text.split(None,1)[1].strip().strip('"'));continue
  if low.startswith(".width "): default_width=int(text.split(None,1)[1]);continue
  if low.startswith(".mode "): default_mode=text.split(None,1)[1].strip().lower();continue
  if text.endswith(":"):
   name=text[:-1].strip()
   if not name or name in labels: raise Basic1048576Error(f"B1048576-ASM: invalid or duplicate label {name}")
   labels[name]=len(lines);continue
  lines.append(text)
 if default_width not in WIDTHS or default_mode not in MODES: raise Basic1048576Error("B1048576-ASM: invalid default width or mode")
 out=[]
 for text in lines:
  parts=[x for x in re.split(r"[\s,]+",text) if x];op=parts[0].upper();a=parts[1:]
  def target(x): return labels[x] if x in labels else number(x)
  width=default_width;mode=default_mode
  if op in {"NOP"}: ins=Instruction(op,width=width,mode=mode)
  elif op=="HALT":ins=Instruction(op,width=width,mode=mode,immediate=number(a[0]) if a else 0)
  elif op=="MOVI":
   if len(a)>2:width=int(a[2])
   ins=Instruction(op,rd=reg(a[0]),width=width,mode=mode,immediate=number(a[1]))
  elif op=="MOV":
   if len(a)>2:width=int(a[2])
   ins=Instruction(op,rd=reg(a[0]),ra=reg(a[1]),width=width,mode=mode)
  elif op in {"ADD","SUB","MUL","DIVU","MODU","AND","OR","XOR","SHL","SHR"}:
   if len(a)>3:width=int(a[3])
   if len(a)>4:mode=a[4].lower()
   ins=Instruction(op,rd=reg(a[0]),ra=reg(a[1]),rb=reg(a[2]),width=width,mode=mode)
  elif op=="NOT":
   if len(a)>2:width=int(a[2])
   if len(a)>3:mode=a[3].lower()
   ins=Instruction(op,rd=reg(a[0]),ra=reg(a[1]),width=width,mode=mode)
  elif op=="CMP":
   if len(a)>2:width=int(a[2])
   ins=Instruction(op,ra=reg(a[0]),rb=reg(a[1]),width=width,mode=mode)
  elif op in {"JMP","JZ","JNZ"}:ins=Instruction(op,width=width,mode=mode,immediate=target(a[0]))
  elif op=="PUSH":ins=Instruction(op,ra=reg(a[0]),width=width,mode=mode)
  elif op=="POP":ins=Instruction(op,rd=reg(a[0]),width=width,mode=mode)
  elif op=="SVC":ins=Instruction(op,ra=reg(a[1]) if len(a)>1 else 0,width=width,mode=mode,immediate=number(a[0]))
  elif op in {"LOAD","STORE"}:
   width=int(a[3]) if len(a)>3 else width
   ins=Instruction(op,rd=reg(a[0]),rb=cap(a[1]),width=width,mode=mode,immediate=number(a[2]))
  else:raise Basic1048576Error(f"B1048576-ASM: unsupported opcode {op}")
  out.append(ins)
 return out,caps,labels
def assemble_source(source:str)->bytes:return assemble(parse_assembly(source)[0])
def build_source(source:str)->bytes:
 ins,caps,labels=parse_assembly(source);return build_executable(assemble(ins),entry_instruction=labels.get("_start",0),capabilities=caps,debug={"labels":labels})
def build_file(source_path:str|Path,output_path:str|Path)->Path:
 out=Path(output_path);out.write_bytes(build_source(Path(source_path).read_text(encoding="utf-8")));return out
