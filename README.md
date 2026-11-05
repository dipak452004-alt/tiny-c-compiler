# Mini Compiler for Tiny C

A mini compiler for a small subset of C ("Tiny C"), written in Python. It takes a Tiny C program through every classic compiler phase and then runs it, printing the result of each phase along the way.

No external libraries are needed, only Python 3.

## Compiler phases

| # | Phase | What it does | Output |
|---|-------|--------------|--------|
| 1 | Lexer | Splits source code into tokens using regular expressions | Token table |
| 2 | Parser | Recursive descent parser that checks the grammar | Abstract syntax tree (AST) |
| 3 | Semantic analyzer | Scoped symbol table, checks declarations | Symbol table, warnings |
| 4 | Code generator | Produces three-address code with constant folding | Intermediate code |
| 5 | Virtual machine | Executes the three-address code | Program output |

## The Tiny C language

- **Type:** `int` (32-bit signed)
- **Declarations:** `int a;`, `int a = 5, b, c = a + 1;`
- **Assignment:** `a = expr;`, `a += expr;` (also `-=`, `*=`, `/=`), `a++;`, `a--;`
- **Operators:** `+ - * / %`, `< > <= >= == !=`, `&& || !`, unary `-`
- **Statements:** `if / else`, `while`, `for`, `{ }` blocks with scoping, `print(expr);`, `return;`
- **Extras:** `// line` and `/* block */` comments, optional `int main() { ... }` wrapper

## Requirements

- Python 3.8 or newer

## Usage

```
python tiny.py <source-file>            # show all phases, then run
python tiny.py <source-file> -q         # only the program output
python tiny.py <source-file> --no-run   # compile only, do not execute
python tiny.py <source-file> -o out.tac # also save the three-address code
```

## Example

`demo.tc`:

```c
int n = 5, fact = 1, i;
for (i = 1; i <= n; i++) {
    fact = fact * i;
}
print(fact);
if (fact > 100 && n % 2 == 1)
    print(1);
else
    print(0);
```

Run it:

```
python tiny.py demo.tc -q
```

Output:

```
120
1
```

Without `-q`, the compiler also prints the tokens, AST, symbol table and three-address code. For example, the `for` loop above compiles to:

```
L1:
  t1 = i <= n
  ifFalse t1 goto L2
  t2 = fact * i
  fact = t2
  t3 = i + 1
  i = t3
  goto L1
L2:
```

## Error handling

Mistakes in the source program are reported with line numbers:

```
Compilation error: Line 2: Variable 'y' is used before declaration
```

```
Runtime error: division by zero
```

The compiler also reports syntax errors (such as a missing `;`), redeclared variables, and warns about division by a constant zero.

## Project structure

```
.
├── tiny.py      # the compiler (lexer, parser, semantic analysis, codegen, VM)
├── sample.tc    # larger example: factorial, Fibonacci, if/else
├── demo.tc      # short example used in the demo
└── README.md
```

## How it works

1. **Lexer:** a single regular expression turns the source into tokens (keyword, identifier, number, operator, symbol) with line numbers.
2. **Parser:** one function per grammar rule builds the AST. Operator precedence is handled level by level, from `||` (lowest) to `* / %` (highest).
3. **Semantic analysis:** walks the AST with a stack of scopes, recording each variable in the symbol table and rejecting undeclared or duplicate names.
4. **Code generation:** emits three-address code (at most one operator per instruction) and folds constant expressions at compile time, e.g. `2 + 3 * 4` becomes `14`.
5. **Execution:** a small virtual machine runs the instructions using 32-bit integers and C-style division that truncates toward zero.

## Limitations

- `&&` and `||` evaluate both sides (no short-circuiting).
- Uninitialised variables start at 0.
- Only the `int` type is supported.

## Future scope

- `float` and `char` types
- Functions and recursion
- Arrays and pointers
- Real assembly (x86) output
- Short-circuit evaluation for `&&` and `||`
