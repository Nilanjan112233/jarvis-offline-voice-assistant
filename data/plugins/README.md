# Jarvis plugins — add your own tools

Put a `.py` file in this folder and restart Jarvis. Every function marked with `@tool(...)` becomes a tool the brain can use.

```python
# data/plugins/dice.py
import random

@tool("Roll dice and say the numbers.", risk="safe", count="how many dice (optional)")
def roll_dice(count="1"):
    n = max(1, min(10, int(count or 1)))
    return "Rolled: " + ", ".join(str(random.randint(1, 6)) for _ in range(n))
```

Rules:
- All parameters are text (strings). Add `(optional)` to the description of optional ones and give them a default.
- Return a short text: that is what the brain reads.
- `risk="confirm"` (the default) shows the Allow/Deny popup every time. Use `risk="safe"` ONLY for tools that just look
  things up and change nothing.
- Plugins run inside Jarvis with your permissions (the Python safety guard does not cover them), so only put code here that
  you wrote or fully trust. Jarvis itself never writes to this folder.
- A file with an error is skipped (Jarvis still starts).
- Files ending in `.off` are ignored — rename `example_dice.py.off` to `example_dice.py` to try the example.
