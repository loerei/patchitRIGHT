"""Unit tests for clean-room on-demand AST symbol resolution."""

import pytest

from patchitright_mcp.symbol_resolver import resolve_symbol


class TestPythonSymbolResolution:
    """Tests for Python AST symbol resolution."""

    def test_top_level_and_async_functions(self):
        src = (
            "def first(a):\n"
            "    return a + 1\n\n"
            "async def second(b):\n"
            "    return b\n"
        )
        m1 = resolve_symbol("mod.py", src, "first")
        assert (m1.start_line, m1.end_line) == (1, 2)
        m2 = resolve_symbol("mod.py", src, "second")
        assert (m2.start_line, m2.end_line) == (4, 5)

    def test_decorated_python_function_includes_decorators(self):
        src = (
            "@first_dec\n"
            "@second_dec(arg=1)\n"
            "def handler(req):\n"
            "    return req\n"
        )
        m = resolve_symbol("mod.py", src, "handler")
        assert (m.start_line, m.end_line) == (1, 4)

    def test_class_and_qualified_methods(self):
        src = (
            "class Alpha:\n"
            "    def run(self):\n"
            "        return 1\n\n"
            "class Beta:\n"
            "    def run(self):\n"
            "        return 2\n"
        )
        cls = resolve_symbol("mod.py", src, "Alpha")
        assert (cls.start_line, cls.end_line) == (1, 3)

        m_beta = resolve_symbol("mod.py", src, "Beta.run")
        assert (m_beta.start_line, m_beta.end_line) == (6, 7)

        # Unqualified ambiguous short name raises clear error with candidates
        with pytest.raises(ValueError, match="Multiple symbols"):
            resolve_symbol("mod.py", src, "run")

        # Line hints disambiguate unqualified short name
        m_hint = resolve_symbol("mod.py", src, "run", hint_start=1, hint_end=4)
        assert (m_hint.start_line, m_hint.end_line) == (2, 3)

    def test_python_overload_prefers_concrete_implementation(self):
        src = (
            "from typing import overload\n\n"
            "@overload\n"
            "def parse(x: int) -> int: ...\n\n"
            "@overload\n"
            "def parse(x: str) -> str: ...\n\n"
            "def parse(x):\n"
            "    return x\n"
        )
        m = resolve_symbol("mod.py", src, "parse")
        assert (m.start_line, m.end_line) == (9, 10)

    def test_symbol_not_found_lists_candidates(self):
        src = "def existing_fn():\n    pass\n"
        with pytest.raises(ValueError, match="not found"):
            resolve_symbol("mod.py", src, "missing_fn")


class TestJsTsSymbolResolution:
    """Tests for JavaScript, TypeScript, and TSX symbol resolution."""

    def test_js_functions_arrows_and_exports(self):
        src = (
            "function plain(a) {\n"
            "  return a;\n"
            "}\n\n"
            "export async function fetchItem(id) {\n"
            "  return id;\n"
            "}\n\n"
            "export default function main() {\n"
            "  return 0;\n"
            "}\n\n"
            "export const add = (a, b) => {\n"
            "  return a + b;\n"
            "};\n\n"
            "const dbl = x => x * 2;\n"
        )
        assert (resolve_symbol("app.js", src, "plain").start_line, resolve_symbol("app.js", src, "plain").end_line) == (1, 3)
        assert (resolve_symbol("app.js", src, "fetchItem").start_line, resolve_symbol("app.js", src, "fetchItem").end_line) == (5, 7)
        assert (resolve_symbol("app.js", src, "main").start_line, resolve_symbol("app.js", src, "main").end_line) == (9, 11)
        assert (resolve_symbol("app.js", src, "add").start_line, resolve_symbol("app.js", src, "add").end_line) == (13, 15)
        assert (resolve_symbol("app.js", src, "dbl").start_line, resolve_symbol("app.js", src, "dbl").end_line) == (17, 17)

    def test_ts_decorators_jsdoc_class_and_methods(self):
        src = (
            "/** Service doc */\n"
            "@Injectable()\n"
            "export class UserService {\n"
            "  /** Update user */\n"
            "  @Log()\n"
            "  async update(id: string) {\n"
            "    return id;\n"
            "  }\n\n"
            "  get count() {\n"
            "    return 1;\n"
            "  }\n\n"
            "  set count(v: number) {\n"
            "    void v;\n"
            "  }\n"
            "}\n"
        )
        cls = resolve_symbol("svc.ts", src, "UserService")
        assert (cls.start_line, cls.end_line) == (1, 17)

        upd = resolve_symbol("svc.ts", src, "UserService.update")
        assert (upd.start_line, upd.end_line) == (4, 8)

        # Getter & setter share property name; line hint disambiguates
        getter = resolve_symbol("svc.ts", src, "count", hint_start=9, hint_end=12)
        assert (getter.start_line, getter.end_line) == (10, 12)
        setter = resolve_symbol("svc.ts", src, "count", hint_start=13, hint_end=16)
        assert (setter.start_line, setter.end_line) == (14, 16)

    def test_ts_overloads_generics_and_tsx(self):
        src = (
            "function format(v: number): string;\n"
            "function format(v: string): string;\n"
            "function format<T>(v: T): string {\n"
            "  return String(v);\n"
            "}\n\n"
            "export const Badge = ({ label }: { label: string }) => {\n"
            "  return <span>{label}</span>;\n"
            "};\n"
        )
        fmt = resolve_symbol("ui.tsx", src, "format")
        assert (fmt.start_line, fmt.end_line) == (3, 5)

        badge = resolve_symbol("ui.tsx", src, "Badge")
        assert (badge.start_line, badge.end_line) == (7, 9)


class TestMultiLanguageAndFallbackSymbolResolution:
    """Tests for Go, Rust, C/C++, Java, C#, CSS, HTML, and no-treesitter fallback."""

    def test_go_functions_methods_and_types(self):
        src = (
            "package srv\n\n"
            "type Server struct {\n"
            "    port int\n"
            "}\n\n"
            "func (s *Server) Start() int {\n"
            "    return s.port\n"
            "}\n\n"
            "func TopHelper() {}\n"
        )
        assert (resolve_symbol("srv.go", src, "Server").start_line, resolve_symbol("srv.go", src, "Server").end_line) == (3, 5)
        assert (resolve_symbol("srv.go", src, "Server.Start").start_line, resolve_symbol("srv.go", src, "Server.Start").end_line) == (7, 9)
        assert (resolve_symbol("srv.go", src, "TopHelper").start_line, resolve_symbol("srv.go", src, "TopHelper").end_line) == (11, 11)

    def test_rust_attributes_structs_and_impl_methods(self):
        src = (
            "#[inline]\n"
            "fn compute() -> u32 {\n"
            "    42\n"
            "}\n\n"
            "struct Pt {\n"
            "    x: i32,\n"
            "}\n\n"
            "impl Pt {\n"
            "    fn get(&self) -> i32 {\n"
            "        self.x\n"
            "    }\n"
            "}\n"
        )
        comp = resolve_symbol("lib.rs", src, "compute")
        assert (comp.start_line, comp.end_line) == (1, 4)
        get_m = resolve_symbol("lib.rs", src, "Pt::get")
        assert (get_m.start_line, get_m.end_line) == (11, 13)

    def test_cpp_definition_beats_forward_declaration(self):
        src = (
            "int calculate(int x);\n\n"
            "namespace math {\n"
            "class Calc {\n"
            "public:\n"
            "    int run();\n"
            "};\n"
            "}\n\n"
            "int math::Calc::run() {\n"
            "    return 1;\n"
            "}\n\n"
            "int calculate(int x) {\n"
            "    return x * 2;\n"
            "}\n"
        )
        calc = resolve_symbol("calc.cpp", src, "calculate")
        assert (calc.start_line, calc.end_line) == (14, 16)
        assert calc.has_body is True

        m = resolve_symbol("calc.cpp", src, "math::Calc::run")
        assert (m.start_line, m.end_line) == (10, 12)

        proto = resolve_symbol("proto.h", "int proto_only(int x);\n", "proto_only")
        assert (proto.start_line, proto.end_line) == (1, 1)
        assert proto.has_body is False

    def test_java_and_csharp(self):
        java_src = (
            "public class Service {\n"
            "    @Override\n"
            "    public int compute() {\n"
            "        return 10;\n"
            "    }\n"
            "}\n"
        )
        jm = resolve_symbol("Service.java", java_src, "Service.compute")
        assert (jm.start_line, jm.end_line) == (2, 5)

        cs_src = (
            "namespace N {\n"
            "    public class C {\n"
            "        public int Add(int a, int b) => a + b;\n"
            "    }\n"
            "}\n"
        )
        cm = resolve_symbol("C.cs", cs_src, "N.C.Add")
        assert (cm.start_line, cm.end_line) == (3, 3)

    def test_css_and_html(self):
        css_src = ".btn {\n  color: red;\n}\n\n#main {\n  margin: 0;\n}\n"
        btn = resolve_symbol("app.css", css_src, ".btn")
        assert (btn.start_line, btn.end_line) == (1, 3)
        main = resolve_symbol("app.css", css_src, "#main")
        assert (main.start_line, main.end_line) == (5, 7)

        html_src = '<div id="app" class="shell">\n  <p>Hello</p>\n</div>\n<img src="a.png" />\n'
        app_div = resolve_symbol("index.html", html_src, "#app")
        assert (app_div.start_line, app_div.end_line) == (1, 3)
        assert app_div.has_body is True

        p_el = resolve_symbol("index.html", html_src, "p")
        assert (p_el.start_line, p_el.end_line) == (2, 2)

        img = resolve_symbol("index.html", html_src, "img")
        assert (img.start_line, img.end_line) == (4, 4)
        assert img.has_body is False

    def test_js_fallback_when_treesitter_unavailable(self, monkeypatch):
        from patchitright_mcp import symbol_resolver

        src = "function helper(x) {\n  return x + 1;\n}\n"
        monkeypatch.setattr(symbol_resolver, "_get_ts_parser", lambda _lang: None)
        m = resolve_symbol("app.js", src, "helper")
        assert (m.start_line, m.end_line) == (1, 3)

def test_getter_preferred_over_setter_when_unhinted():
    js_src = "class Box {\n  get v() {\n    return 1;\n  }\n  set v(x) {\n    void x;\n  }\n}\n"
    gm = resolve_symbol("box.js", js_src, "v")
    assert (gm.start_line, gm.end_line) == (2, 4)

    py_src = (
        "class Box:\n"
        "    @property\n"
        "    def v(self):\n"
        "        return 1\n\n"
        "    @v.setter\n"
        "    def v(self, x):\n"
        "        pass\n"
    )
    pm = resolve_symbol("box.py", py_src, "v")
    assert (pm.start_line, pm.end_line) == (2, 4)
