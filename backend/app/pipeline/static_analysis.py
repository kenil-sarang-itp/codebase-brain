"""
Static analysis — call-graph construction.

Before any documentation is generated, a static-analysis pass walks the whole
codebase and builds a call graph: for every function, *what it calls* and *what
calls it*. This graph drives Level-3 flow tracing, "critical function"
detection, and validation-agent call-chain checks.

Two analysers, picked per language (spec section 4):
    * `python` files → Python's built-in `ast` module — the most accurate
      possible analysis for Python.
    * other languages → Tree-sitter, walking the syntax tree for call nodes.

Known limitation (documented in the spec): only *direct* calls are detected.
Dynamic dispatch, reflection, and cross-service HTTP calls are not — accuracy
is ~90-95% on well-structured code. We surface this honestly rather than
pretend completeness.
"""

from __future__ import annotations

import ast

from app.core.logging import get_logger
from app.pipeline.chunking.language_detector import TREE_SITTER_LANGUAGES, detect_language

logger = get_logger(__name__)


class FunctionInfo:
    """Analysis result for one function: its location and the names it calls."""

    __slots__ = ("name", "file_path", "language", "calls")

    def __init__(
        self, name: str, file_path: str, language: str, calls: set[str]
    ) -> None:
        self.name = name
        self.file_path = file_path
        self.language = language
        self.calls = calls


class _PythonCallVisitor(ast.NodeVisitor):
    """AST visitor that records every function/class and the calls it makes.

    For each `FunctionDef`/`AsyncFunctionDef`/`ClassDef` it collects the set of
    callee names referenced anywhere in that definition's body.
    """

    def __init__(self, file_path: str) -> None:
        self._file_path = file_path
        self.functions: list[FunctionInfo] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._record(node)

    def visit_AsyncFunctionDef(  # noqa: N802
        self, node: ast.AsyncFunctionDef
    ) -> None:
        self._record(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        # Record the class itself, then descend so its methods are recorded too.
        self._record(node, descend=True)

    def _record(self, node: ast.AST, descend: bool = False) -> None:
        """Record one definition node and the calls inside it."""
        name = getattr(node, "name", "<anonymous>")
        calls: set[str] = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                callee = self._callee_name(inner.func)
                if callee:
                    calls.add(callee)
        self.functions.append(
            FunctionInfo(name, self._file_path, "python", calls)
        )
        if descend:
            # Visit children so nested FunctionDefs (methods) are also recorded.
            for child in ast.iter_child_nodes(node):
                self.visit(child)

    @staticmethod
    def _callee_name(func_node: ast.AST) -> str | None:
        """Extract a callee name from a Call's func node.

        Handles plain calls `foo()` and attribute calls `obj.method()`,
        returning the rightmost identifier in both cases.
        """
        if isinstance(func_node, ast.Name):
            return func_node.id
        if isinstance(func_node, ast.Attribute):
            return func_node.attr
        return None


class StaticAnalyzer:
    """Builds a per-file list of `FunctionInfo` from source code."""

    def analyze_file(self, file_path: str, content: str) -> list[FunctionInfo]:
        """Analyse one file and return its functions with their callees.

        Never raises — a syntactically broken file yields an empty list and a
        warning, so one bad file cannot abort an entire indexing run.
        """
        language = detect_language(file_path)
        try:
            if language == "python":
                result = self._analyze_python(file_path, content)
            elif language in TREE_SITTER_LANGUAGES:
                result = self._analyze_with_tree_sitter(
                    file_path, content, language
                )
            else:
                logger.debug(
                    "Static analysis: skipping %s — unsupported language '%s'",
                    file_path, language,
                )
                return []

            if result:
                logger.info(
                    "Static analysis: %s (%s) → %d functions found",
                    file_path, language, len(result),
                )
            else:
                logger.debug(
                    "Static analysis: %s (%s) → 0 functions (no definitions detected)",
                    file_path, language,
                )
            return result
        except SyntaxError as exc:
            logger.warning(
                "Static analysis: %s (%s) skipped — syntax error: %s. "
                "Content preview (first 120 chars): %r",
                file_path, language, exc, content[:120],
            )
            return []
        except Exception as exc:
            # Log the FULL traceback — a swallowed exception here was the cause
            # of empty call graphs going undiagnosed.
            logger.error(
                "Static analysis FAILED for %s (%s): %s",
                file_path, language, exc, exc_info=True,
            )
            return []

    # -------------------------------------------------------------- python --
    def _analyze_python(
        self, file_path: str, content: str
    ) -> list[FunctionInfo]:
        """Analyse a Python file with the built-in `ast` module (most accurate)."""
        tree = ast.parse(content, filename=file_path)
        visitor = _PythonCallVisitor(file_path)
        visitor.visit(tree)
        return visitor.functions

    # --------------------------------------------------------- tree-sitter --

    # Definition node types per language family.
    # JavaScript/TypeScript have many ways to define a component or function:
    #   - function_declaration:     function foo() {}
    #   - arrow_function:           const foo = () => {}
    #   - variable_declarator:      const Foo = () => {} (wraps arrow_function)
    #   - method_definition:        class methods
    #   - class_declaration:        class Foo {}
    #   - export_statement:         export default function/class
    _JS_DEFINITION_TYPES = frozenset({
        "function_declaration",
        "function",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "class",
        "generator_function_declaration",
        "generator_function",
    })

    _GENERIC_DEFINITION_KEYWORDS = ("function", "method", "class")

    def _analyze_with_tree_sitter(
        self, file_path: str, content: str, language: str
    ) -> list[FunctionInfo]:
        """Analyse a non-Python file by walking its Tree-sitter syntax tree.

        Handles JavaScript/TypeScript React codebases specifically — arrow
        function components, named function components, class components, and
        hooks are all detected. Falls back to generic keyword matching for
        other languages.
        """
        try:
            from tree_sitter_languages import get_parser
        except Exception as exc:
            # ERROR level (not debug) — a missing tree-sitter is the difference
            # between a working call graph and an empty one.
            logger.error(
                "tree_sitter_languages import FAILED — call graph will be "
                "empty. Error: %s", exc, exc_info=True,
            )
            return []

        try:
            parser = get_parser(language)
        except Exception as exc:
            logger.error(
                "get_parser('%s') FAILED for %s — Error: %s",
                language, file_path, exc, exc_info=True,
            )
            return []

        source = content.encode("utf-8", errors="ignore")
        tree = parser.parse(source)
        is_js = language in ("javascript", "typescript")

        def is_definition(node) -> bool:
            if is_js:
                return node.type in self._JS_DEFINITION_TYPES
            return (
                any(kw in node.type for kw in self._GENERIC_DEFINITION_KEYWORDS)
                and "call" not in node.type
            )

        def is_call(node) -> bool:
            return "call_expression" in node.type or (
                not is_js and "call" in node.type
            )

        def def_name(node, parent=None) -> str:
            """Extract the best name for a definition node.

            For arrow functions assigned to a variable (const Foo = () => {}),
            the name lives on the parent variable_declarator's name field.
            """
            # Direct name field (function_declaration, class_declaration).
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                return name_node.text.decode("utf-8", errors="ignore")

            # Arrow function: name comes from the parent variable declarator.
            if node.type == "arrow_function" and parent is not None:
                if parent.type == "variable_declarator":
                    n = parent.child_by_field_name("name")
                    if n is not None:
                        return n.text.decode("utf-8", errors="ignore")

            return f"anonymous_{node.start_point[0] + 1}"

        def collect_calls(node, acc: set[str]) -> None:
            """Recursively collect callee names under a definition node."""
            for child in node.children:
                if is_call(child):
                    # call_expression: function field is the callee.
                    fn_node = child.child_by_field_name("function")
                    if fn_node is not None:
                        text = fn_node.text.decode("utf-8", errors="ignore")
                        # Strip member access: foo.bar() → bar
                        callee = text.split(".")[-1].split("(")[0].strip()
                        if callee and not callee.startswith("<"):
                            acc.add(callee)
                collect_calls(child, acc)

        functions: list[FunctionInfo] = []

        def extract_from_variable_declarator(vd_node) -> None:
            """Handle: const Foo = () => {} and const Foo = function() {}

            The name is the declarator's identifier; the definition is the
            arrow_function or function child.
            """
            name_node = vd_node.child_by_field_name("name")
            if name_node is None:
                return
            var_name = name_node.text.decode("utf-8", errors="ignore")
            # Find the arrow_function or function expression value.
            value_node = vd_node.child_by_field_name("value")
            if value_node is None:
                return
            if value_node.type in ("arrow_function", "function"):
                calls: set[str] = set()
                collect_calls(value_node, calls)
                functions.append(FunctionInfo(var_name, file_path, language, calls))

        def walk(node) -> None:
            for child in node.children:
                t = child.type
                if t == "function_declaration":
                    # function Foo() {}
                    name = def_name(child)
                    calls: set[str] = set()
                    collect_calls(child, calls)
                    functions.append(FunctionInfo(name, file_path, language, calls))
                    walk(child)

                elif t in ("lexical_declaration", "variable_declaration"):
                    # const/let/var declarations — look for arrow/function values.
                    for declarator in child.children:
                        if declarator.type == "variable_declarator":
                            extract_from_variable_declarator(declarator)
                    walk(child)

                elif t == "class_declaration":
                    # class Foo { ... } — record the class and its methods.
                    name = def_name(child)
                    calls: set[str] = set()
                    collect_calls(child, calls)
                    functions.append(FunctionInfo(name, file_path, language, calls))
                    walk(child)

                elif t == "method_definition":
                    # Methods inside a class body.
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        method_name = name_node.text.decode("utf-8", errors="ignore")
                        calls = set()
                        collect_calls(child, calls)
                        functions.append(
                            FunctionInfo(method_name, file_path, language, calls)
                        )
                    walk(child)

                elif not is_js and is_definition(child):
                    # Generic fallback for non-JS languages.
                    name = def_name(child)
                    calls = set()
                    collect_calls(child, calls)
                    functions.append(FunctionInfo(name, file_path, language, calls))
                    walk(child)

                else:
                    walk(child)

        walk(tree.root_node)
        logger.info(
            "Tree-sitter found %d definitions in %s (%s)",
            len(functions), file_path, language,
        )

        # Defense in depth: if tree-sitter walked the tree but found nothing
        # (grammar quirk, unusual syntax), fall back to the regex analyzer so
        # the call graph is never silently empty.
        if not functions and is_js:
            logger.warning(
                "Tree-sitter found 0 definitions in %s — using regex fallback.",
                file_path,
            )
            return self._analyze_js_with_regex(file_path, content, language)

        return functions

    # ----------------------------------------------------------- regex --
    def _analyze_js_with_regex(
        self, file_path: str, content: str, language: str
    ) -> list[FunctionInfo]:
        """Regex-based JS/TS analyzer — a robust fallback for tree-sitter.

        Not as precise as a real parser, but it reliably finds the common
        React/JS definition patterns and the calls inside each, so the call
        graph is populated even when tree-sitter yields nothing.
        """
        import re

        functions: list[FunctionInfo] = []

        # Patterns that declare a function/component/method.
        # Each captures the definition NAME in group 1.
        definition_patterns = [
            # function Foo(...)  /  export function Foo(...)  /  async function
            re.compile(r'(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+([A-Za-z_$][\w$]*)'),
            # const Foo = (...) =>  /  let Foo = async (...) =>
            re.compile(r'(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>'),
            # const Foo = function
            re.compile(r'(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?function'),
            # class Foo
            re.compile(r'(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)'),
        ]
        # Any identifier immediately followed by ( is treated as a call.
        call_pattern = re.compile(r'\b([A-Za-z_$][\w$]*)\s*\(')
        # Identifiers that are JS keywords, not real calls.
        keywords = {
            "if", "for", "while", "switch", "catch", "return", "function",
            "typeof", "await", "super", "new", "void", "async", "const",
            "let", "var", "else", "do", "yield", "delete", "in", "of",
            "throw", "case", "import", "export", "default", "class",
        }

        lines = content.split("\n")
        for pattern in definition_patterns:
            for match in pattern.finditer(content):
                name = match.group(1)
                # Find the body: from the match to the next ~80 lines or EOF.
                start = content[: match.start()].count("\n")
                body = "\n".join(lines[start : start + 80])
                calls = {
                    c for c in call_pattern.findall(body)
                    if c not in keywords and c != name
                }
                functions.append(
                    FunctionInfo(name, file_path, language, calls)
                )

        # Deduplicate by name (keep the one with the most calls).
        by_name: dict[str, FunctionInfo] = {}
        for fn in functions:
            existing = by_name.get(fn.name)
            if existing is None or len(fn.calls) > len(existing.calls):
                by_name[fn.name] = fn

        result = list(by_name.values())
        logger.info(
            "Regex fallback found %d definitions in %s", len(result), file_path
        )
        return result
