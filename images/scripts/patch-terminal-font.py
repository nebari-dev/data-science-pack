"""Patch JupyterLab's JS bundle to use Nerd Font and gate terminal on font load."""
import glob
import sys

pattern = sys.argv[1]
files = glob.glob(pattern)
if not files:
    print(f"No files matching {pattern}")
    sys.exit(1)

for path in files:
    with open(path) as f:
        js = f.read()

    # 1. Replace default terminal font
    old_font = "fontFamily:'Menlo, Consolas, \"DejaVu Sans Mono\", monospace',fontSize:13,lineHeight:1"
    new_font = '''fontFamily:"'FiraCode Nerd Font', 'Fira Code', monospace",fontSize:14,lineHeight:1.2'''
    if old_font in js:
        js = js.replace(old_font, new_font)
        print(f"Patched default font in {path}")
    else:
        print(f"WARNING: default font string not found in {path}")

    # 2. Gate xterm.open() on font availability
    # Before: if(!this._termOpened){this._term.open(this.node)
    # After:  if(!this._termOpened){if(!document.fonts.check('14px "FiraCode Nerd Font"')){requestAnimationFrame(()=>this.update());return}this._term.open(this.node)
    old_open = "if(!this._termOpened){this._term.open(this.node)"
    new_open = (
        'if(!this._termOpened){'
        'if(!document.fonts.check(\'14px "FiraCode Nerd Font"\')){'
        'requestAnimationFrame(()=>this.update());return}'
        'this._term.open(this.node)'
    )
    if old_open in js:
        js = js.replace(old_open, new_open)
        print(f"Patched xterm.open() gate in {path}")
    else:
        print(f"WARNING: xterm.open() string not found in {path}")

    with open(path, "w") as f:
        f.write(js)
