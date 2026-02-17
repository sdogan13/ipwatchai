import re
import os

FILE = os.path.join("C:", os.sep, "Users", "701693", "turk_patent", "templates", "partials", "_leads_panel.html")

with open(FILE, "r", encoding="utf-8") as f:
    text = f.read()

lines = text.split(chr(10))
out = []

for line in lines:

    # Stats cards: bg-white -> card-base
    if "bg-white rounded-xl p-5 border border-gray-100 shadow-sm" in line:
        line = line.replace(
            chr(39) + "bg-white rounded-xl p-5 border border-gray-100 shadow-sm" + chr(39),
            chr(39) + "card-base rounded-xl p-5" + chr(39)
        )

    out.append(line)

print("Test: ", len(out), "lines")