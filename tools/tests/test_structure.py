#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check that device/code.py still defines everything it needs.

This exists because three separate edits deleted working code without anything
noticing. Each replaced a span found between two markers - "from this function
to that assignment" - and each span silently contained more than intended. The
file still parsed, the reader still booted, and the damage only showed up as
odd behaviour on the device: text in the wrong font and clipped at the top,
because reader_font was never assigned, and a crash on opening the menu,
because PICKER_ROWS is set by the function that had gone missing.

A parse check cannot catch that. This can.
"""
import ast
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", ".."))

FUNCTIONS = """
    log_step uart_log usb_attached list_fonts font_label load_font_choice
    save_font_choice load_reader_font get_string_width draw_text
    draw_text_justified new_canvas begin_frame end_frame to_font fit_text
    list_books list_epubs epub_txt_path book_title read_page_stream
    get_page_lines render_page_buffer render_list picker_label render_message
    render_goto_screen render_sleep_screen choose_from_list run_picker run_goto
    run_fonts open_picker convert_epub turn_forward turn_back jump_to_percent
    reflow_current_page switch_to_book save_position build_display
    teardown_display build_keys enter_light_sleep enter_deep_sleep
    display_page show_restored_page prefetch_neighbours _hyphenate_word
    raw_to_volts get_battery_status
""".split()

ASSIGNMENTS = """
    PANELS BOARDS FONTS FONT_SUBS SKIP_FILES BOOK_DIRS GOTO_ROW FONTS_ROW
    SAVE_EVERY_N_TURNS JUSTIFY_TEXT ENABLE_EPUB ENABLE_HYPHENATION
    _font_index _FONT_NVM_OFFSET WIDTH
""".split()


def main():
    src = open(os.path.join(ROOT, "device", "code.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    names = set()
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
                elif isinstance(t, ast.Tuple):
                    names.update(e.id for e in t.elts if isinstance(e, ast.Name))
        elif isinstance(n, (ast.If, ast.Try)):
            for sub in ast.walk(n):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)

    missing_f = [f for f in FUNCTIONS if f not in funcs]
    missing_a = [a for a in ASSIGNMENTS if a not in names]
    print("top-level functions: %d found, %d expected" % (len(funcs), len(FUNCTIONS)))
    if missing_f:
        print("  MISSING: %s" % ", ".join(missing_f))
    print("module-level names checked: %d" % len(ASSIGNMENTS))
    if missing_a:
        print("  MISSING: %s" % ", ".join(missing_a))

    # every global a function assigns must exist somewhere at module level
    declared = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Global):
            declared.update(n.names)
    undeclared = sorted(g for g in declared if g not in names and g not in funcs)
    if undeclared:
        print("  globals declared in functions but never assigned at module "
              "level: %s" % ", ".join(undeclared))

    bad = bool(missing_f or missing_a)
    print("\n%s" % ("structure intact" if not bad else "STRUCTURE BROKEN"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
