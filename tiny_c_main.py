#!/usr/bin/env python3
"""
Tiny C Mini Compiler
====================
Phases:  Lexer -> Parser (AST) -> Semantic Analysis -> Three-Address Code
         (with constant folding) -> Execution (TAC virtual machine)

Supported Tiny C subset
-----------------------
  * Type:         int (32-bit signed)
  * Declarations: int a;  int a = 5, b, c = a + 1;
  * Assignment:   a = expr;  a += expr;  -= *= /=   a++;  a--;
  * Operators:    + - * / %   < > <= >= == !=   && || !   unary -
  * Statements:   if / else, while, for, blocks { }, print(expr);, return;
  * Optional wrapper:  int main() { ... }
  * Comments:     // line   and   /* block */

Usage
-----
  python tinyc.py program.tc            # show all phases + run
  python tinyc.py program.tc -q         # only the program's output
  python tinyc.py program.tc --no-run   # compile only
  python tinyc.py program.tc -o out.tac # also save the intermediate code
"""

import re
import sys
import argparse
from collections import namedtuple


# ----------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------
class CompileError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(f"Line {line}: {msg}" if line else msg)


# ----------------------------------------------------------------------------
# PHASE 1: LEXER
# ----------------------------------------------------------------------------
Token = namedtuple("Token", "type value line")

KEYWORDS = {"int", "if", "else", "while", "for", "print", "return"}

TOKEN_SPEC = [
    ("COMMENT", r"//[^\n]*|/\*.*?\*/"),
    ("BADCOMMENT", r"/\*"),
    ("NUMBER", r"\d+"),
    ("IDENT", r"[A-Za-z_]\w*"),
    ("OPERATOR", r"==|!=|<=|>=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|[+\-*/%<>=!]"),
    ("SYMBOL", r"[(){};,]"),
    ("NEWLINE", r"\n"),
    ("SPACE", r"[ \t\r]+"),
    ("BAD", r"."),
]
TOKEN_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOKEN_SPEC), re.S)


def lex(src):
    tokens, line = [], 1
    for m in TOKEN_RE.finditer(src):
        kind, val = m.lastgroup, m.group()
        if kind == "SPACE":
            continue
        if kind == "NEWLINE":
            line += 1
            continue
        if kind == "COMMENT":
            line += val.count("\n")
            continue
        if kind == "BADCOMMENT":
            raise CompileError("Unterminated /* comment", line)
        if kind == "BAD":
            raise CompileError(f"Unexpected character {val!r}", line)
        if kind == "IDENT":
            kind = "KEYWORD" if val in KEYWORDS else "IDENTIFIER"
        tokens.append(Token(kind, val, line))
    tokens.append(Token("EOF", "", line))
    return tokens


# ----------------------------------------------------------------------------
# PHASE 2: PARSER  (recursive descent, builds an AST of tuples)
#
# AST node shapes:
#   ('program', [stmts])             ('block', [stmts])
#   ('decls', [decl, ...])           ('decl', name, init|None, line)
#   ('assign', name, expr, line)     ('print', expr, line)
#   ('if', cond, then, else|None)    ('while', cond, body)
#   ('return', expr|None)
#   ('num', value)                   ('var', name, line)
#   ('bin', op, left, right, line)   ('un', op, expr, line)
# ----------------------------------------------------------------------------
class Parser:
    LEVELS = [("||",), ("&&",), ("==", "!="), ("<", ">", "<=", ">="),
              ("+", "-"), ("*", "/", "%")]

    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    # -- helpers
    def peek(self):
        return self.t[self.i]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def check(self, *vals):
        return self.peek().value in vals

    def expect(self, val):
        tok = self.peek()
        if tok.value != val:
            found = tok.value or "end of file"
            raise CompileError(f"Expected '{val}' but found '{found}'", tok.line)
        return self.next()

    def ident(self):
        tok = self.peek()
        if tok.type != "IDENTIFIER":
            found = tok.value or "end of file"
            raise CompileError(f"Expected identifier but found '{found}'", tok.line)
        return self.next()

    # -- grammar
    def program(self):
        if self.check("int") and self.t[self.i + 1].value == "main":
            self.next()
            self.next()
            self.expect("(")
            self.expect(")")
            body = self.block()
            if self.peek().type != "EOF":
                raise CompileError("Unexpected code after end of main()", self.peek().line)
            return ("program", [body])
        stmts = []
        while self.peek().type != "EOF":
            stmts.append(self.statement())
        return ("program", stmts)

    def block(self):
        self.expect("{")
        stmts = []
        while not self.check("}"):
            if self.peek().type == "EOF":
                raise CompileError("Missing '}' before end of file", self.peek().line)
            stmts.append(self.statement())
        self.expect("}")
        return ("block", stmts)

    def decls(self):
        self.expect("int")
        items = []
        while True:
            name = self.ident()
            init = None
            if self.check("="):
                self.next()
                init = self.expr()
            items.append(("decl", name.value, init, name.line))
            if self.check(","):
                self.next()
                continue
            break
        return ("decls", items)

    def simple(self):
        """assignment / compound assignment / ++ / -- (no trailing ';')"""
        tok = self.ident()
        name, line = tok.value, tok.line
        op = self.peek().value
        if op == "=":
            self.next()
            return ("assign", name, self.expr(), line)
        if op in ("+=", "-=", "*=", "/="):
            self.next()
            rhs = self.expr()
            return ("assign", name, ("bin", op[0], ("var", name, line), rhs, line), line)
        if op in ("++", "--"):
            self.next()
            return ("assign", name, ("bin", op[0], ("var", name, line), ("num", 1), line), line)
        raise CompileError(f"Expected assignment after '{name}'", line)

    def statement(self):
        tok = self.peek()
        if self.check("{"):
            return self.block()
        if self.check(";"):
            self.next()
            return ("block", [])
        if self.check("int"):
            node = self.decls()
            self.expect(";")
            return node
        if self.check("if"):
            self.next()
            self.expect("(")
            cond = self.expr()
            self.expect(")")
            then = self.statement()
            other = None
            if self.check("else"):
                self.next()
                other = self.statement()
            return ("if", cond, then, other)
        if self.check("while"):
            self.next()
            self.expect("(")
            cond = self.expr()
            self.expect(")")
            return ("while", cond, self.statement())
        if self.check("for"):
            self.next()
            self.expect("(")
            init = None
            if not self.check(";"):
                init = self.decls() if self.check("int") else self.simple()
            self.expect(";")
            cond = ("num", 1) if self.check(";") else self.expr()
            self.expect(";")
            update = None if self.check(")") else self.simple()
            self.expect(")")
            body = self.statement()
            loop = ("while", cond, ("block", [body] + ([update] if update else [])))
            return ("block", ([init] if init else []) + [loop])
        if self.check("print"):
            self.next()
            self.expect("(")
            e = self.expr()
            self.expect(")")
            self.expect(";")
            return ("print", e, tok.line)
        if self.check("return"):
            self.next()
            e = None if self.check(";") else self.expr()
            self.expect(";")
            return ("return", e)
        if tok.type == "IDENTIFIER":
            node = self.simple()
            self.expect(";")
            return node
        raise CompileError(f"Unexpected '{tok.value or 'end of file'}'", tok.line)

    # -- expressions (precedence climbing by levels)
    def expr(self, lvl=0):
        if lvl == len(self.LEVELS):
            return self.unary()
        left = self.expr(lvl + 1)
        while self.check(*self.LEVELS[lvl]):
            op = self.next()
            right = self.expr(lvl + 1)
            left = ("bin", op.value, left, right, op.line)
        return left

    def unary(self):
        if self.check("-", "!", "+"):
            op = self.next()
            e = self.unary()
            return e if op.value == "+" else ("un", op.value, e, op.line)
        return self.primary()

    def primary(self):
        tok = self.peek()
        if tok.type == "NUMBER":
            self.next()
            return ("num", int(tok.value))
        if tok.type == "IDENTIFIER":
            self.next()
            return ("var", tok.value, tok.line)
        if self.check("("):
            self.next()
            e = self.expr()
            self.expect(")")
            return e
        raise CompileError(f"Expected expression but found '{tok.value or 'end of file'}'", tok.line)


# ----------------------------------------------------------------------------
# PHASE 3: SEMANTIC ANALYSIS  (scoped symbol table)
# ----------------------------------------------------------------------------
class Semantic:
    def __init__(self):
        self.scopes = [{}]
        self.table = []      # (name, type, scope level, declared at line)
        self.warnings = []

    def declare(self, name, line):
        if name in self.scopes[-1]:
            raise CompileError(f"Variable '{name}' is already declared in this scope", line)
        self.scopes[-1][name] = "int"
        self.table.append((name, "int", len(self.scopes) - 1, line))

    def lookup(self, name, line):
        if not any(name in sc for sc in self.scopes):
            raise CompileError(f"Variable '{name}' is used before declaration", line)

    def visit(self, n):
        k = n[0]
        if k == "program":
            for c in n[1]:
                self.visit(c)
        elif k == "block":
            self.scopes.append({})
            for c in n[1]:
                self.visit(c)
            self.scopes.pop()
        elif k == "decls":
            for d in n[1]:
                self.visit(d)
        elif k == "decl":
            if n[2]:
                self.visit(n[2])         # initializer is checked before the name exists
            self.declare(n[1], n[3])
        elif k == "assign":
            self.visit(n[2])
            self.lookup(n[1], n[3])
        elif k == "print":
            self.visit(n[1])
        elif k == "if":
            self.visit(n[1])
            self.visit(n[2])
            if n[3]:
                self.visit(n[3])
        elif k == "while":
            self.visit(n[1])
            self.visit(n[2])
        elif k == "return":
            if n[1]:
                self.visit(n[1])
        elif k == "var":
            self.lookup(n[1], n[2])
        elif k == "bin":
            self.visit(n[2])
            self.visit(n[3])
            if n[1] in ("/", "%") and n[3] == ("num", 0):
                self.warnings.append(f"Line {n[4]}: division by constant zero")
        elif k == "un":
            self.visit(n[2])


# ----------------------------------------------------------------------------
# ALU shared by the optimizer (constant folding) and the virtual machine
# ----------------------------------------------------------------------------
def wrap(x):
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x >= (1 << 31) else x


def trunc_div(a, b):                      # C-style: truncates toward zero
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def alu(op, a, b):
    if op == "+": r = a + b
    elif op == "-": r = a - b
    elif op == "*": r = a * b
    elif op in ("/", "%"):
        if b == 0:
            raise ZeroDivisionError
        q = trunc_div(a, b)
        r = q if op == "/" else a - b * q
    elif op == "<": r = int(a < b)
    elif op == ">": r = int(a > b)
    elif op == "<=": r = int(a <= b)
    elif op == ">=": r = int(a >= b)
    elif op == "==": r = int(a == b)
    elif op == "!=": r = int(a != b)
    elif op == "&&": r = int(a != 0 and b != 0)
    elif op == "||": r = int(a != 0 or b != 0)
    else:
        raise ValueError(op)
    return wrap(r)


IS_CONST = re.compile(r"-?\d+$")


# ----------------------------------------------------------------------------
# PHASE 4: INTERMEDIATE CODE (three-address code) + constant folding
# Instruction = (op, arg1, arg2, result)
# ----------------------------------------------------------------------------
class CodeGen:
    def __init__(self):
        self.code = []
        self.scopes = [{}]
        self.used = {}
        self.nt = 0
        self.nl = 0
        self.folded = 0

    def temp(self):
        self.nt += 1
        return f"t{self.nt}"

    def label(self):
        self.nl += 1
        return f"L{self.nl}"

    def emit(self, op, a=None, b=None, r=None):
        self.code.append((op, a, b, r))

    def declare(self, name):
        count = self.used.get(name, 0)
        self.used[name] = count + 1
        uniq = name if count == 0 else f"{name}_{count}"
        if re.fullmatch(r"[tL]\d+", uniq):       # avoid clashing with temps/labels
            uniq += "_v"
        self.scopes[-1][name] = uniq
        return uniq

    def lookup(self, name):
        for sc in reversed(self.scopes):
            if name in sc:
                return sc[name]
        raise CompileError(f"Unknown variable '{name}'")

    def generate(self, ast):
        self.stmt(ast)
        return self.code

    def stmt(self, n):
        k = n[0]
        if k == "program":
            for c in n[1]:
                self.stmt(c)
        elif k == "block":
            self.scopes.append({})
            for c in n[1]:
                self.stmt(c)
            self.scopes.pop()
        elif k == "decls":
            for d in n[1]:
                self.stmt(d)
        elif k == "decl":
            val = self.expr(n[2]) if n[2] else "0"   # uninitialised ints start at 0
            self.emit("=", val, None, self.declare(n[1]))
        elif k == "assign":
            val = self.expr(n[2])
            self.emit("=", val, None, self.lookup(n[1]))
        elif k == "print":
            self.emit("print", self.expr(n[1]))
        elif k == "if":
            cond = self.expr(n[1])
            l_else = self.label()
            self.emit("iffalse", cond, None, l_else)
            self.stmt(n[2])
            if n[3]:
                l_end = self.label()
                self.emit("goto", None, None, l_end)
                self.emit("label", None, None, l_else)
                self.stmt(n[3])
                self.emit("label", None, None, l_end)
            else:
                self.emit("label", None, None, l_else)
        elif k == "while":
            l_start, l_end = self.label(), self.label()
            self.emit("label", None, None, l_start)
            cond = self.expr(n[1])
            self.emit("iffalse", cond, None, l_end)
            self.stmt(n[2])
            self.emit("goto", None, None, l_start)
            self.emit("label", None, None, l_end)
        elif k == "return":
            if n[1]:
                self.expr(n[1])
            self.emit("halt")

    def expr(self, n):
        k = n[0]
        if k == "num":
            return str(n[1])
        if k == "var":
            return self.lookup(n[1])
        if k == "un":
            x = self.expr(n[2])
            if IS_CONST.match(x):                      # constant folding
                self.folded += 1
                return str(alu("-", 0, int(x)) if n[1] == "-" else int(int(x) == 0))
            t = self.temp()
            self.emit("neg" if n[1] == "-" else "not", x, None, t)
            return t
        if k == "bin":
            l, r = self.expr(n[2]), self.expr(n[3])
            if IS_CONST.match(l) and IS_CONST.match(r):
                try:
                    v = alu(n[1], int(l), int(r))
                    self.folded += 1
                    return str(v)
                except ZeroDivisionError:
                    pass                               # leave it for a runtime error
            t = self.temp()
            self.emit(n[1], l, r, t)
            return t
        raise CompileError(f"Internal error: bad node {k}")


# ----------------------------------------------------------------------------
# PHASE 5: EXECUTION (TAC virtual machine)
# ----------------------------------------------------------------------------
def run(code, max_steps=2_000_000, echo=True):
    labels = {ins[3]: i for i, ins in enumerate(code) if ins[0] == "label"}
    env, out = {}, []
    pc = steps = 0

    def val(x):
        return int(x) if IS_CONST.match(x) else env[x]

    while pc < len(code):
        steps += 1
        if steps > max_steps:
            raise RuntimeError("Step limit exceeded (possible infinite loop)")
        op, a, b, r = code[pc]
        pc += 1
        if op == "label":
            continue
        if op == "=":
            env[r] = val(a)
        elif op == "print":
            out.append(val(a))
            if echo:
                print(val(a))
        elif op == "goto":
            pc = labels[r]
        elif op == "iffalse":
            if val(a) == 0:
                pc = labels[r]
        elif op == "halt":
            break
        elif op == "neg":
            env[r] = wrap(-val(a))
        elif op == "not":
            env[r] = int(val(a) == 0)
        else:
            try:
                env[r] = alu(op, val(a), val(b))
            except ZeroDivisionError:
                raise RuntimeError("Runtime error: division by zero")
    return out, env


# ----------------------------------------------------------------------------
# Pretty printers
# ----------------------------------------------------------------------------
def ast_lines(n, d=0):
    p, k, out = "  " * d, n[0], []
    if k in ("program", "block"):
        out.append(p + k.capitalize())
        for c in n[1]:
            out += ast_lines(c, d + 1)
    elif k == "decls":
        for c in n[1]:
            out += ast_lines(c, d)
    elif k == "decl":
        out.append(f"{p}Declare int {n[1]}")
        if n[2]:
            out += ast_lines(n[2], d + 1)
    elif k == "assign":
        out.append(f"{p}Assign {n[1]} =")
        out += ast_lines(n[2], d + 1)
    elif k == "print":
        out.append(p + "Print")
        out += ast_lines(n[1], d + 1)
    elif k == "if":
        out += [p + "If", p + "  Condition:"] + ast_lines(n[1], d + 2)
        out += [p + "  Then:"] + ast_lines(n[2], d + 2)
        if n[3]:
            out += [p + "  Else:"] + ast_lines(n[3], d + 2)
    elif k == "while":
        out += [p + "While", p + "  Condition:"] + ast_lines(n[1], d + 2)
        out += [p + "  Body:"] + ast_lines(n[2], d + 2)
    elif k == "return":
        out.append(p + "Return")
        if n[1]:
            out += ast_lines(n[1], d + 1)
    elif k == "num":
        out.append(f"{p}Num {n[1]}")
    elif k == "var":
        out.append(f"{p}Var {n[1]}")
    elif k == "bin":
        out.append(f"{p}BinOp '{n[1]}'")
        out += ast_lines(n[2], d + 1) + ast_lines(n[3], d + 1)
    elif k == "un":
        out.append(f"{p}UnaryOp '{n[1]}'")
        out += ast_lines(n[2], d + 1)
    return out


def fmt_tac(ins):
    op, a, b, r = ins
    if op == "label":
        return f"{r}:"
    if op == "goto":
        return f"    goto {r}"
    if op == "iffalse":
        return f"    ifFalse {a} goto {r}"
    if op == "print":
        return f"    print {a}"
    if op == "halt":
        return "    halt"
    if op == "=":
        return f"    {r} = {a}"
    if op in ("neg", "not"):
        return f"    {r} = {'-' if op == 'neg' else '!'}{a}"
    return f"    {r} = {a} {op} {b}"


def banner(title):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------
def compile_and_run(src, quiet=False, execute=True, tac_out=None):
    tokens = lex(src)
    if not quiet:
        banner("PHASE 1: LEXICAL ANALYSIS (tokens)")
        print(f"{'LINE':<6}{'TYPE':<14}VALUE")
        for t in tokens[:-1]:
            print(f"{t.line:<6}{t.type:<14}{t.value}")

    ast = Parser(tokens).program()
    if not quiet:
        banner("PHASE 2: SYNTAX ANALYSIS (abstract syntax tree)")
        print("\n".join(ast_lines(ast)))

    sem = Semantic()
    sem.visit(ast)
    if not quiet:
        banner("PHASE 3: SEMANTIC ANALYSIS (symbol table)")
        print(f"{'NAME':<12}{'TYPE':<8}{'SCOPE':<8}LINE")
        for name, typ, scope, line in sem.table:
            print(f"{name:<12}{typ:<8}{scope:<8}{line}")
        for w in sem.warnings:
            print("warning:", w)
        print("No semantic errors.")

    gen = CodeGen()
    code = gen.generate(ast)
    if not quiet:
        banner("PHASE 4: INTERMEDIATE CODE (three-address code)")
        print("\n".join(fmt_tac(i) for i in code))
        print(f"\n{len(code)} instructions, {gen.folded} constant expression(s) folded.")

    if tac_out:
        with open(tac_out, "w") as f:
            f.write("\n".join(fmt_tac(i) for i in code) + "\n")
        if not quiet:
            print(f"TAC written to {tac_out}")

    if execute:
        if not quiet:
            banner("PHASE 5: EXECUTION (program output)")
        out, env = run(code)
        if not quiet:
            print("\nFinal variable values:",
                  {k: v for k, v in env.items() if not re.fullmatch(r"t\d+", k)})


def main():
    ap = argparse.ArgumentParser(description="Tiny C mini compiler")
    ap.add_argument("file", help="Tiny C source file (.tc)")
    ap.add_argument("-q", "--quiet", action="store_true", help="only print program output")
    ap.add_argument("--no-run", action="store_true", help="compile only, do not execute")
    ap.add_argument("-o", "--tac-out", help="save three-address code to a file")
    args = ap.parse_args()

    try:
        with open(args.file) as f:
            src = f.read()
    except OSError as e:
        sys.exit(f"Cannot open file: {e}")

    try:
        compile_and_run(src, args.quiet, not args.no_run, args.tac_out)
    except CompileError as e:
        sys.exit(f"Compilation error: {e}")
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()