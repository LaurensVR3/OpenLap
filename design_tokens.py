# design_tokens.py — colours, fonts, and the font() helper
# All other modules import from here so changes propagate everywhere.

BG      = "#0d0f18"    # page background
SIDEBAR = "#13151f"    # sidebar
CARD    = "#1a1d2e"    # card / panel background
CARD2   = "#21253a"    # elevated card
BORDER  = "#2a2e47"    # subtle border
ACC     = "#4f8ef7"    # primary accent (blue)
ACC2    = "#00d4ff"    # secondary accent (cyan)
OK      = "#22c55e"    # success green
WARN    = "#f59e0b"    # amber warning
ERR     = "#ef4444"    # red error
TEXT    = "#e8eaf6"    # primary text
TEXT2   = "#8892b0"    # muted text
TEXT3   = "#4a5180"    # very muted

FN      = "Segoe UI"
MONO    = "Consolas"


def font(size=10, bold=False, mono=False):
    family = MONO if mono else FN
    weight = "bold" if bold else "normal"
    return (family, size, weight)
