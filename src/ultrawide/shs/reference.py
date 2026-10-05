from __future__ import annotations
"""Deterministic reference implementation of the BASIC-1048576 hosted virtual architecture."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
import hashlib, json, struct

ARCHITECTURE = "BASIC-1048576"
ARCHITECTURE_VERSION = "0.2.0"
PROFILE_BASE = 2
PROFILE_EXPONENT = 20
WORD_BITS = 1048576
WORD_BYTES = WORD_BITS // 8
CONTROL_BYTES = 32
INSTRUCTION_BYTES = CONTROL_BYTES + WORD_BYTES
REGISTER_COUNT = 16
CAPABILITY_COUNT = 16
WIDTHS = tuple(1 << i for i in range(3, PROFILE_EXPONENT + 1))
WIDTH_INDEX = {w:i for i,w in enumerate(WIDTHS)}
MASK = (1 << WORD_BITS) - 1
SIGN = 1 << (WORD_BITS - 1)
MAGIC = b"B1048576E\0"
FORMAT_VERSION = 1

class Basic1048576Error(Exception): pass
class VerificationError(Basic1048576Error): pass
class VMFault(Basic1048576Error): pass

OPCODES = {
 "NOP":0x00,"MOVI":0x01,"MOV":0x02,
 "ADD":0x10,"SUB":0x11,"MUL":0x12,"DIVU":0x13,"MODU":0x14,
 "AND":0x15,"OR":0x16,"XOR":0x17,"NOT":0x18,"SHL":0x19,"SHR":0x1A,
 "CMP":0x20,"JMP":0x21,"JZ":0x22,"JNZ":0x23,
 "LOAD":0x30,"STORE":0x31,"PUSH":0x32,"POP":0x33,
 "SVC":0x50,"HALT":0xFF,
}
NAMES_BY_OPCODE={v:k for k,v in OPCODES.items()}
MODES={"wrapping":0,"checked":1,"saturating":2,"trapping":3}
MODES_BY_ID={v:k for k,v in MODES.items()}
SVC_PRINT_U65536=1
SVC_PRINT_I65536=2
SVC_PRINT_HEX=3
SVC_PRINT_CHAR=4
SVC_EVIDENCE_MARK=5

def canonical_json(obj:object)->bytes:
 return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")

def mask_for(width:int)->int:
 if width not in WIDTHS: raise Basic1048576Error(f"B1048576-WIDTH: unsupported width {width}")
 return (1 << width) - 1

def normalize(value:int,width:int=WORD_BITS)->int: return value & mask_for(width)

def signed(value:int,width:int=WORD_BITS)->int:
 value &= mask_for(width); sign=1 << (width-1)
 return value-(1<<width) if value & sign else value

def full_hex(value:int)->str: return "0x"+normalize(value).to_bytes(WORD_BYTES,"big").hex()

def digest_word(value:int)->str: return hashlib.sha256(normalize(value).to_bytes(WORD_BYTES,"little")).hexdigest()

def apply_mode(raw:int,width:int,mode:str)->tuple[int,bool]:
 maximum=mask_for(width); overflow=raw<0 or raw>maximum
 if mode=="wrapping": return raw & maximum, overflow
 if mode=="saturating": return (0 if raw<0 else maximum if raw>maximum else raw), overflow
 if mode in {"checked","trapping"}:
  if overflow: raise VMFault(f"B1048576-ARITH-{mode.upper()}: result outside unsigned {width}-bit range")
  return raw, False
 raise VMFault(f"B1048576-ARITH: unknown mode {mode}")

@dataclass(frozen=True)
class Instruction:
 name:str; rd:int=0; ra:int=0; rb:int=0; width:int=WORD_BITS; mode:str="wrapping"; immediate:int=0
 def encode(self)->bytes:
  if self.name not in OPCODES: raise Basic1048576Error(f"B1048576-ASM: unknown opcode {self.name}")
  if any(not 0<=v<REGISTER_COUNT for v in (self.rd,self.ra,self.rb)): raise Basic1048576Error("B1048576-ASM: register index outside R0..R15")
  if self.width not in WIDTH_INDEX: raise Basic1048576Error(f"B1048576-ASM: invalid width {self.width}")
  if self.mode not in MODES: raise Basic1048576Error(f"B1048576-ASM: invalid arithmetic mode {self.mode}")
  if not 0<=self.immediate<=MASK: raise Basic1048576Error("B1048576-ASM: immediate outside 1,048,576-bit unsigned range")
  header=bytearray(CONTROL_BYTES)
  header[0]=OPCODES[self.name]; header[1]=self.rd; header[2]=self.ra; header[3]=self.rb
  header[4]=WIDTH_INDEX[self.width]; header[5]=MODES[self.mode]
  return bytes(header)+self.immediate.to_bytes(WORD_BYTES,"little")
 @classmethod
 def decode(cls,data:bytes)->"Instruction":
  if len(data)!=INSTRUCTION_BYTES: raise VMFault(f"B1048576-DECODE: instruction must be {INSTRUCTION_BYTES} bytes")
  h=data[:CONTROL_BYTES]
  if any(h[6:]): raise VMFault("B1048576-DECODE: reserved control bytes must be zero")
  opcode,rd,ra,rb,widx,mid=h[:6]
  if opcode not in NAMES_BY_OPCODE: raise VMFault(f"B1048576-DECODE: invalid opcode 0x{opcode:02x}")
  if max(rd,ra,rb)>=REGISTER_COUNT: raise VMFault("B1048576-DECODE: register index out of range")
  if widx>=len(WIDTHS): raise VMFault("B1048576-DECODE: invalid width index")
  if mid not in MODES_BY_ID: raise VMFault("B1048576-DECODE: invalid arithmetic mode")
  return cls(NAMES_BY_OPCODE[opcode],rd,ra,rb,WIDTHS[widx],MODES_BY_ID[mid],int.from_bytes(data[CONTROL_BYTES:],"little"))

def assemble(instructions:Iterable[Instruction])->bytes: return b"".join(i.encode() for i in instructions)
def disassemble(code:bytes)->list[Instruction]:
 if len(code)%INSTRUCTION_BYTES: raise VerificationError("B1048576-CODE: code is not instruction aligned")
 return [Instruction.decode(code[i:i+INSTRUCTION_BYTES]) for i in range(0,len(code),INSTRUCTION_BYTES)]

def build_executable(code:bytes,*,entry_instruction:int=0,capabilities:Iterable[str]=(),debug:dict|None=None)->bytes:
 ins=disassemble(code)
 if not ins or entry_instruction<0 or entry_instruction>=len(ins): raise VerificationError("B1048576-EXE: entry point outside code")
 header={"architecture":ARCHITECTURE,"architecture_version":ARCHITECTURE_VERSION,"format_version":FORMAT_VERSION,
 "profile_base":PROFILE_BASE,"profile_exponent":PROFILE_EXPONENT,"word_bits":WORD_BITS,"endianness":"little","instruction_bytes":INSTRUCTION_BYTES,"entry_instruction":entry_instruction,
 "capabilities":sorted(set(capabilities)),"code_size":len(code),"code_sha256":hashlib.sha256(code).hexdigest(),"debug":debug or {}}
 hb=canonical_json(header); prefix=MAGIC+struct.pack("<I",len(hb))+hb+code
 return prefix+hashlib.sha256(prefix).digest()

def verify_executable(blob:bytes)->dict:
 if len(blob)<len(MAGIC)+4+32 or not blob.startswith(MAGIC): raise VerificationError("B1048576-EXE: invalid magic or truncated executable")
 n=struct.unpack("<I",blob[len(MAGIC):len(MAGIC)+4])[0]; start=len(MAGIC)+4; end=start+n
 if end+32>len(blob): raise VerificationError("B1048576-EXE: truncated header")
 try: header=json.loads(blob[start:end])
 except Exception as exc: raise VerificationError(f"B1048576-EXE: invalid header: {exc}") from exc
 code=blob[end:-32]
 if hashlib.sha256(blob[:-32]).digest()!=blob[-32:]: raise VerificationError("B1048576-EXE: integrity digest mismatch")
 required={"architecture":ARCHITECTURE,"format_version":FORMAT_VERSION,"profile_base":PROFILE_BASE,"profile_exponent":PROFILE_EXPONENT,"word_bits":WORD_BITS,"endianness":"little","instruction_bytes":INSTRUCTION_BYTES,"code_size":len(code),"code_sha256":hashlib.sha256(code).hexdigest()}
 for k,v in required.items():
  if header.get(k)!=v: raise VerificationError(f"B1048576-EXE: header mismatch for {k}")
 ins=disassemble(code); entry=header.get("entry_instruction")
 if not isinstance(entry,int) or entry<0 or entry>=len(ins): raise VerificationError("B1048576-EXE: invalid entry point")
 return {"header":header,"code":code,"sha256":hashlib.sha256(blob).hexdigest()}

@dataclass
class Capability:
 base:int; length:int; permissions:frozenset[str]; generation:int=1; revoked:bool=False
 def check(self,offset:int,size:int,permission:str)->int:
  if self.revoked: raise VMFault("B1048576-CAP: capability revoked")
  if permission not in self.permissions: raise VMFault(f"B1048576-CAP: missing {permission} permission")
  if offset<0 or size<0 or offset+size>self.length: raise VMFault("B1048576-CAP: bounds violation")
  return self.base+offset

@dataclass
class VMState:
 registers:list[int]=field(default_factory=lambda:[0]*REGISTER_COUNT)
 pc:int=0; sp:int=0; fp:int=0; zero:bool=False; negative:bool=False; carry:bool=False; overflow:bool=False; halted:bool=False; exit_code:int=0
 def public(self)->dict:
  return {"registers_preview":[full_hex(x) if i < 2 else None for i,x in enumerate(self.registers)],"register_sha256":[digest_word(x) for x in self.registers],"pc":self.pc,"sp":self.sp,"fp":self.fp,
  "flags":{"Z":self.zero,"N":self.negative,"C":self.carry,"V":self.overflow},"halted":self.halted,"exit_code":self.exit_code}

class Basic1048576VM:
 def __init__(self,memory_bytes:int=16*1024*1024,capabilities:Iterable[str]=(),deterministic:bool=True):
  if memory_bytes<WORD_BYTES or memory_bytes>512*1024*1024: raise ValueError("B1048576-VM: hosted memory limit must be 131072..536870912 bytes")
  self.memory=bytearray(memory_bytes); self.capabilities=set(capabilities); self.deterministic=deterministic
  self.state=VMState(sp=memory_bytes,fp=memory_bytes); self.cap_table=[None]*CAPABILITY_COUNT
  self.cap_table[0]=Capability(0,memory_bytes,frozenset({"read","write"}))
  self.stdout=[]; self.trace=[]; self.evidence_marks=[]
 def _cap(self,index:int)->Capability:
  if not 0<=index<CAPABILITY_COUNT or self.cap_table[index] is None: raise VMFault(f"B1048576-CAP: invalid capability C{index}")
  return self.cap_table[index]
 def _set_flags(self,value:int,width:int,overflow:bool=False):
  value &= mask_for(width); self.state.zero=value==0; self.state.negative=bool(value&(1<<(width-1))); self.state.carry=overflow; self.state.overflow=overflow
 def _write(self,index:int,value:int,width:int=WORD_BITS): self.state.registers[index]=normalize(value,width)
 def _push(self,value:int):
  if self.state.sp<WORD_BYTES: raise VMFault("B1048576-STACK: overflow")
  self.state.sp-=WORD_BYTES; self.memory[self.state.sp:self.state.sp+WORD_BYTES]=normalize(value).to_bytes(WORD_BYTES,"little")
 def _pop(self)->int:
  if self.state.sp+WORD_BYTES>len(self.memory): raise VMFault("B1048576-STACK: underflow")
  v=int.from_bytes(self.memory[self.state.sp:self.state.sp+WORD_BYTES],"little"); self.state.sp+=WORD_BYTES; return v
 def snapshot(self)->dict:
  return {"architecture":ARCHITECTURE,"architecture_version":ARCHITECTURE_VERSION,"memory_sha256":hashlib.sha256(self.memory).hexdigest(),"state":self.state.public(),"stdout":"".join(self.stdout),"trace_sha256":hashlib.sha256(canonical_json(self.trace)).hexdigest()}
 def run(self,executable:bytes|str|Path,max_steps:int=1_000_000)->dict:
  blob=Path(executable).read_bytes() if isinstance(executable,(str,Path)) else executable; verified=verify_executable(blob)
  missing=sorted(set(verified["header"].get("capabilities",[]))-self.capabilities)
  if missing: raise VMFault("B1048576-CAP: missing capabilities: "+", ".join(missing))
  insns=disassemble(verified["code"]); self.state.pc=verified["header"]["entry_instruction"]
  steps=0
  while not self.state.halted:
   if steps>=max_steps: raise VMFault("B1048576-LIMIT: instruction limit exceeded")
   if not 0<=self.state.pc<len(insns): raise VMFault("B1048576-PC: program counter outside code")
   pc=self.state.pc; ins=insns[pc]; self.state.pc+=1; self._execute(ins)
   self.trace.append({"step":steps,"pc":pc,"instruction":{"name":ins.name,"rd":ins.rd,"ra":ins.ra,"rb":ins.rb,"width":ins.width,"mode":ins.mode,"immediate_sha256":digest_word(ins.immediate)},"r0_sha256":digest_word(self.state.registers[0]),"flags":{"Z":self.state.zero,"N":self.state.negative,"C":self.state.carry,"V":self.state.overflow}})
   steps+=1
  out="".join(self.stdout); tb=canonical_json(self.trace)
  return {"architecture":ARCHITECTURE,"stdout":out,"exit_code":self.state.exit_code,"steps":steps,"state":self.state.public(),"trace":self.trace,"trace_sha256":hashlib.sha256(tb).hexdigest(),"output_sha256":hashlib.sha256(out.encode()).hexdigest(),"executable_sha256":verified["sha256"]}
 def _execute(self,ins:Instruction):
  r=self.state.registers; n=ins.name; w=ins.width; m=ins.mode
  if n=="NOP": return
  if n=="MOVI": self._write(ins.rd,ins.immediate,w); self._set_flags(r[ins.rd],w); return
  if n=="MOV": self._write(ins.rd,r[ins.ra],w); self._set_flags(r[ins.rd],w); return
  if n in {"ADD","SUB","MUL","DIVU","MODU","AND","OR","XOR","NOT","SHL","SHR"}:
   a=r[ins.ra]&mask_for(w); b=r[ins.rb]&mask_for(w)
   if n=="ADD": raw=a+b
   elif n=="SUB": raw=a-b
   elif n=="MUL": raw=a*b
   elif n=="DIVU":
    if b==0: raise VMFault("B1048576-ARITH: division by zero")
    raw=a//b
   elif n=="MODU":
    if b==0: raise VMFault("B1048576-ARITH: division by zero")
    raw=a%b
   elif n=="AND": raw=a&b
   elif n=="OR": raw=a|b
   elif n=="XOR": raw=a^b
   elif n=="NOT": raw=(~a)&mask_for(w)
   elif n=="SHL": raw=a<<(b%w)
   else: raw=a>>(b%w)
   value,ov=apply_mode(raw,w,m); self._write(ins.rd,value,w); self._set_flags(value,w,ov); return
  if n=="CMP":
   a=r[ins.ra]&mask_for(w); b=r[ins.rb]&mask_for(w); self.state.zero=a==b; self.state.negative=a<b; self.state.carry=a>=b; self.state.overflow=False; return
  if n in {"JMP","JZ","JNZ"}:
   take=n=="JMP" or (n=="JZ" and self.state.zero) or (n=="JNZ" and not self.state.zero)
   if take: self.state.pc=ins.immediate
   return
  if n=="LOAD":
   size=w//8; addr=self._cap(ins.rb).check(ins.immediate,size,"read"); self._write(ins.rd,int.from_bytes(self.memory[addr:addr+size],"little"),w); self._set_flags(r[ins.rd],w); return
  if n=="STORE":
   size=w//8; addr=self._cap(ins.rb).check(ins.immediate,size,"write"); self.memory[addr:addr+size]=(r[ins.rd]&mask_for(w)).to_bytes(size,"little"); return
  if n=="PUSH": self._push(r[ins.ra]); return
  if n=="POP": self._write(ins.rd,self._pop()); return
  if n=="SVC":
   service=ins.immediate; value=r[ins.ra]
   if service==SVC_PRINT_U65536: self.stdout.append(str(value))
   elif service==SVC_PRINT_I65536: self.stdout.append(str(signed(value)))
   elif service==SVC_PRINT_HEX: self.stdout.append(full_hex(value))
   elif service==SVC_PRINT_CHAR:
    if value>0x10FFFF: raise VMFault("B1048576-SVC: invalid Unicode scalar")
    self.stdout.append(chr(value))
   elif service==SVC_EVIDENCE_MARK: self.evidence_marks.append(digest_word(value))
   else: raise VMFault(f"B1048576-SVC: unsupported service {service}")
   return
  if n=="HALT": self.state.exit_code=ins.immediate & 0xffffffff; self.state.halted=True; return
  raise VMFault(f"B1048576-VM: unimplemented instruction {n}")
