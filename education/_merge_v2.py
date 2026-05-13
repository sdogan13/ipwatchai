import json, sys
sys.stdout.reconfigure(encoding='utf-8')

# Load source and partial
with open(r'C:\Users\701693\turk_patent\education\sorular.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
with open(r'C:\Users\701693\turk_patent\education\sorular_e_options_partial.json', 'r', encoding='utf-8') as f:
    e_options = json.load(f)

# Validate every question has an E option
missing = [i for i in range(len(data)) if str(i) not in e_options]
if missing:
    print(f"ERROR: missing E options for indices: {missing[:20]} (total {len(missing)})")
    sys.exit(1)

# Merge - append option E to each question's options array
for i, q in enumerate(data):
    e = e_options[str(i)]
    q['options'].append({
        "id": "E",
        "text": e['text'],
        "status": "unchecked",
        "shortFeedback": e['shortFeedback']
    })

# Validate result
right_count_issues = 0
opt_count_issues = 0
for i, q in enumerate(data):
    if len(q['options']) != 5:
        opt_count_issues += 1
    rights = sum(1 for o in q['options'] if o['status'] == 'Right answer')
    if rights != 1:
        right_count_issues += 1
        if right_count_issues < 5:
            print(f"  Q{i}: {rights} right answers")

print(f"Questions with != 5 options: {opt_count_issues}")
print(f"Questions with != 1 right answer: {right_count_issues}")

# Write
out_path = r'C:\Users\701693\turk_patent\education\sorular_v2.json'
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

import os
size = os.path.getsize(out_path)
print(f"\nWrote: {out_path}")
print(f"Size: {size:,} bytes ({size/1024/1024:.2f} MB)")
print(f"Questions: {len(data)} (each with 5 options)")
