"""
V7: pre-strip "Sizin" wrapper before mid-text catch-all, broader "sorunuz" matcher,
final sweep for residual "Sizin Söz konusu" artifacts.
"""
import json, re, sys
sys.stdout.reconfigure(encoding='utf-8')

Q = r"[\"'“”‘’„‚«»]"
QC_DOT = rf"{Q}[^{Q[1:-1]}]{{0,300}}?{Q}"

NOUN_AFTER_QUOTE = r"(?:şıkk[ıi]|seçene[ğg]i|cevab[ıi]n[ıi]?n?|durum[uü]?(?:n[ıi]?n?)?|ifade(?:si(?:n[ıi]?n?)?)?|süre(?:si(?:n[ıi]?n?)?)?|yöntem(?:i(?:n[ıi]?n?)?)?|şey(?:i(?:n[ıi]?n?)?)?|faaliyet(?:i(?:n[ıi]?n?)?)?|seçim(?:i(?:n[ıi]?n?)?)?|tercih(?:i(?:n[ıi]?n?)?)?)"


OPENER_REPLACEMENTS = [
    (re.compile(r"^Verdiğiniz\s+cevab[ıi]n[ıi]?n?\s+yanl[ıi]ş\s+olmas[ıi]n[ıi]n(\s+temel)?\s+(nedeni|sebebi)([,\.\s])", re.IGNORECASE),
     r'Bu sorunun doğru cevabının "{right}" olmasının\1 \2\3'),
    (re.compile(r"^Cevab[ıi]n[ıi]z[ıi]?n?\s+yanl[ıi]ş\s+olmas[ıi]n[ıi]n(\s+temel)?\s+(nedeni|sebebi)([,\.\s])", re.IGNORECASE),
     r'Bu sorunun doğru cevabının "{right}" olmasının\1 \2\3'),
    (re.compile(r"^S[ıi]navdaki\s+cevab[ıi]n[ıi]z[ıi]?n?\s+yanl[ıi]ş\s+olmas[ıi]n[ıi]n(\s+temel)?\s+(nedeni|sebebi)([,\.\s])", re.IGNORECASE),
     r'Bu sorunun doğru cevabının "{right}" olmasının\1 \2\3'),
    (re.compile(r"^Soruyu\s+yanl[ıi]ş\s+cevaplaman[ıi]z[ıi]?n?(\s+temel)?\s+(nedeni|sebebi)([,\.\s])", re.IGNORECASE),
     r'Bu sorunun doğru cevabının "{right}" olmasının\1 \2\3'),
    (re.compile(r"^Yan[ıi]t[ıi]n[ıi]z[ıi]?n?\s+yanl[ıi]ş\s+olmas[ıi]n[ıi]n(\s+temel)?\s+(nedeni|sebebi)([,\.\s])", re.IGNORECASE),
     r'Bu sorunun doğru cevabının "{right}" olmasının\1 \2\3'),
    (re.compile(r"^S[ıi]navdaki\s+sorunuza\s+verdi[ğg]iniz.{0,400}?yanl[ıi]ş\s+olmas[ıi]n[ıi]n(\s+temel)?\s+(nedeni|sebebi)([,\.\s])", re.IGNORECASE | re.UNICODE),
     r'Bu sorunun doğru cevabının "{right}" olmasının\1 \2\3'),
    (re.compile(r"^Sizin\s+seçti[ğg]iniz\s+(?:şıkk[ıi]n[ıi]?n?|seçene[ğg]in?|cevab[ıi]n[ıi]?n?)\s+yanl[ıi]ş\s+olmas[ıi]n[ıi]n(\s+temel)?\s+(nedeni|sebebi)([,\.\s])", re.IGNORECASE),
     r'Bu sorunun doğru cevabının "{right}" olmasının\1 \2\3'),
    (re.compile(rf"^Sizin\s+seçti[ğg]iniz\s+{QC_DOT}\s+(?:şıkk[ıi]|seçene[ğg]i|cevab[ıi])\s+yanl[ıi]şt[ıi]r\s+çünkü\s+", re.IGNORECASE | re.UNICODE),
     r'Bu sorunun doğru cevabının "{right}" olmasının nedeni şudur: '),
    (re.compile(rf"^Seçti[ğg]iniz\s+{QC_DOT}\s+(?:şıkk[ıi]|seçene[ğg]i|cevab[ıi])\s+yanl[ıi]şt[ıi]r\s+çünkü\s+", re.IGNORECASE | re.UNICODE),
     r'Bu sorunun doğru cevabının "{right}" olmasının nedeni şudur: '),
    (re.compile(rf"^Sizin\s+i[şs]aretledi[ğg]iniz\s+{QC_DOT}[^.]*?\.\s+", re.IGNORECASE | re.UNICODE),
     r'Bu sorunun doğru cevabı "{right}" olarak belirlenmiştir. '),
    (re.compile(r"^Soruda\s+sizden[^\.]{0,300}?(?:istemi[şs]tir|istenmi[şs]tir)\.\s+", re.IGNORECASE | re.UNICODE),
     r'Bu sorunun doğru cevabı "{right}" olarak belirlenmiştir. '),
    (re.compile(r"^Seçti[ğg]iniz\s+cevap\s+yanl[ıi]ş\s+çünkü\s+", re.IGNORECASE),
     r'Bu sorunun doğru cevabının "{right}" olmasının nedeni şudur: '),
    (re.compile(r"^S[ıi]navdaki\s+cevab[ıi]n[ıi]z.{0,200}?yanl[ıi]ş\s+olmas[ıi]n[ıi]n(\s+temel)?\s+(nedeni|sebebi)([,\.\s])", re.IGNORECASE | re.UNICODE),
     r'Bu sorunun doğru cevabının "{right}" olmasının\1 \2\3'),
]

TRAILING_PATTERNS = [
    re.compile(r"\n\n(?:Seçti[ğg]iniz|Sizin\s+seçti[ğg]iniz|Sizin\s+i[şs]aretledi[ğg]iniz|Bir\s+önceki\s+sorunuzda|Daha\s+önceki\s+(?:bir\s+)?sorunuzda)[^\n]*(?:\n[^\n]+)*$", re.IGNORECASE | re.UNICODE),
]

# Phase A: chat artifacts + sentence-level "your-question" rewrites + "Sizin <wrapper>" pre-strip
PHASE_A_FIXES = [
    (re.compile(r",?\s*sohbetimizin\s+önceki[^.,]*[.,]\s*", re.IGNORECASE | re.UNICODE), " "),
    (re.compile(r",?\s*(?:Bir|Daha)\s+önceki\s+(?:bir\s+)?sorunuzda[^.]*\.\s*", re.IGNORECASE | re.UNICODE), ""),
    (re.compile(r",?\s*daha\s+önce\s+konuştu[ğg]umuz\s+(?:gibi|üzere)\s*,?\s*", re.IGNORECASE | re.UNICODE), " "),
    (re.compile(r",?\s*detayl[ıi]ca\s+konuştu[ğg]umuz\s+gibi\s*,?\s*", re.IGNORECASE | re.UNICODE), " "),
    (re.compile(r",?\s*belirtti[ğg]im\s+gibi\s*,?\s*", re.IGNORECASE | re.UNICODE), " "),
    (re.compile(r",?\s*Daha\s+önceki\s+(?:bir\s+)?benzer\s+sorunuzda\s+da\s+", re.IGNORECASE | re.UNICODE), ""),
    # "sorunuz" + suffixes
    (re.compile(r"\bsorunuzdaki\b", re.IGNORECASE | re.UNICODE), "sorudaki"),
    (re.compile(r"\bsorunuzdan\b", re.IGNORECASE | re.UNICODE), "sorudan"),
    (re.compile(r"\bsorunuza\b", re.IGNORECASE | re.UNICODE), "soruya"),
    (re.compile(r"\bsorunuzun\b", re.IGNORECASE | re.UNICODE), "sorunun"),
    (re.compile(r"\bsorunuzda\b", re.IGNORECASE | re.UNICODE), "soruda"),
    (re.compile(r"\bsorunuz\b", re.IGNORECASE | re.UNICODE), "soru"),
    # PRE-STRIP "Sizin/sizin" wrapper before any "seçtiğiniz/verdiğiniz/işaretlediğiniz"
    (re.compile(r"\bSizin\s+(?=(?:seçti[ğg]iniz|verdi[ğg]iniz|i[şs]aretledi[ğg]iniz)\b)", re.UNICODE), ""),
    (re.compile(r"\bsizin\s+(?=(?:seçti[ğg]iniz|verdi[ğg]iniz|i[şs]aretledi[ğg]iniz)\b)", re.UNICODE), ""),
    # Standalone "Sizin" before "söz konusu" (artifact from earlier passes — but apply early too)
    (re.compile(r"\bSizin\s+söz\s+konusu\b", re.UNICODE), "Söz konusu"),
    (re.compile(r"\bsizin\s+söz\s+konusu\b", re.UNICODE), "söz konusu"),
    # "sizden" 2nd person
    (re.compile(r"\bsizden\b", re.IGNORECASE), "soruyu çözenden"),
    (re.compile(r"\bSizden\b", re.IGNORECASE), "Soruyu çözenden"),
]

# Phase B: handle "seçtiğiniz/verdiğiniz" with quoted phrase + noun
PHASE_B_FIXES = [
    (re.compile(rf"\bSeçti[ğg]iniz\s+{QC_DOT}\s+{NOUN_AFTER_QUOTE}\b", re.UNICODE), "Söz konusu seçenek"),
    (re.compile(rf"\bseçti[ğg]iniz\s+{QC_DOT}\s+{NOUN_AFTER_QUOTE}\b", re.UNICODE), "söz konusu seçenek"),
    (re.compile(rf"\bSeçti[ğg]iniz\s+({QC_DOT})\s+şeklinde", re.UNICODE), r"Söz konusu \1 şeklinde"),
    (re.compile(rf"\bseçti[ğg]iniz\s+({QC_DOT})\s+şeklinde", re.UNICODE), r"söz konusu \1 şeklinde"),
    (re.compile(rf"\bVerdi[ğg]iniz\s+{QC_DOT}\s+(?:cevab[ıi]n[ıi]?n?|seçene[ğg]i)\b", re.IGNORECASE | re.UNICODE), "Söz konusu seçenek"),
    (re.compile(rf"\bverdi[ğg]iniz\s+{QC_DOT}\s+(?:cevab[ıi]n[ıi]?n?|seçene[ğg]i)\b", re.IGNORECASE | re.UNICODE), "söz konusu seçenek"),
]

# Phase C: handle bare "seçtiğiniz QUOTE" (no noun) and bare "seçtiğiniz word"
PHASE_C_FIXES = [
    (re.compile(rf"\bSeçti[ğg]iniz\s+({QC_DOT})", re.UNICODE), r"Söz konusu \1 seçeneği"),
    (re.compile(rf"\bseçti[ğg]iniz\s+({QC_DOT})", re.UNICODE), r"söz konusu \1 seçeneği"),
    (re.compile(r"\bSeçti[ğg]iniz\s+cevap\b", re.IGNORECASE), "Söz konusu seçenek"),
    (re.compile(r"\bseçti[ğg]iniz\s+cevap\b", re.IGNORECASE), "söz konusu seçenek"),
    (re.compile(r"\bSeçti[ğg]iniz\s+şık\b", re.IGNORECASE), "Söz konusu seçenek"),
    (re.compile(r"\bseçti[ğg]iniz\s+şık\b", re.IGNORECASE), "söz konusu seçenek"),
    (re.compile(r"\bSeçti[ğg]iniz\s+ifade\b", re.IGNORECASE), "Söz konusu ifade"),
    (re.compile(r"\bseçti[ğg]iniz\s+ifade\b", re.IGNORECASE), "söz konusu ifade"),
    (re.compile(r"\bSeçti[ğg]iniz\s+(?=[A-Za-zÇĞİÖŞÜçğıöşü])", re.IGNORECASE), "Söz konusu "),
    (re.compile(r"\bseçti[ğg]iniz\s+(?=[A-Za-zÇĞİÖŞÜçğıöşü])", re.IGNORECASE), "söz konusu "),
    (re.compile(r"\bVerdi[ğg]iniz\s+cevap\b", re.IGNORECASE), "Söz konusu seçenek"),
    (re.compile(r"\bverdi[ğg]iniz\s+cevap\b", re.IGNORECASE), "söz konusu seçenek"),
    (re.compile(rf"\bverdi[ğg]iniz\s+{QC_DOT}", re.IGNORECASE | re.UNICODE), "söz konusu seçenek"),
    (re.compile(r"\bSizin\s+i[şs]aretledi[ğg]iniz\b", re.IGNORECASE), "Yanlış olarak değerlendirilen"),
    (re.compile(r"\bsizin\s+i[şs]aretledi[ğg]iniz\b", re.IGNORECASE), "yanlış olarak değerlendirilen"),
    (re.compile(r"\bi[şs]aretledi[ğg]iniz\b", re.IGNORECASE), "yanlış olarak değerlendirilen"),
]

# Phase D: final sweep — re-handle any "Sizin Söz konusu" artifacts that arose from prior passes
PHASE_D_FIXES = [
    (re.compile(r"\bSizin\s+Söz\s+konusu\b", re.UNICODE), "Söz konusu"),
    (re.compile(r"\bsizin\s+söz\s+konusu\b", re.UNICODE), "söz konusu"),
    (re.compile(r"\bSizin\s+söz\s+konusu\b", re.UNICODE), "Söz konusu"),
]

PERSONALIZATION_PROBES = [
    "Verdiğiniz", "verdiğiniz",
    "Cevabınız", "cevabınızın",
    "Sizin ", " sizin ",
    "Seçtiğiniz ", "seçtiğiniz ",
    "Soruyu yanlış",
    "Sınavdaki cevab",
    "işaretlediğiniz",
    "sohbetimizin",
    "konuştuğumuz gibi",
    "sorunuz",
    "sizden",
]


def clean_explanation(text, right_answer_text):
    if not text:
        return text, False
    out = text.strip()
    fired = False
    for pat, template in OPENER_REPLACEMENTS:
        repl = template.replace("{right}", right_answer_text)
        new, n = pat.subn(repl, out, count=1)
        if n > 0:
            out = new
            fired = True
            break
    for pat in TRAILING_PATTERNS:
        out = pat.sub("", out).rstrip()
    for pat, repl in PHASE_A_FIXES:
        out = pat.sub(repl, out)
    for pat, repl in PHASE_B_FIXES:
        out = pat.sub(repl, out)
    for pat, repl in PHASE_C_FIXES:
        out = pat.sub(repl, out)
    for pat, repl in PHASE_D_FIXES:
        out = pat.sub(repl, out)
    out = re.sub(r"(?<=[.\n]\s)söz konusu", "Söz konusu", out)
    out = re.sub(r"^söz konusu", "Söz konusu", out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r" +\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip(), fired


def clean_summary(text, right_answer_text):
    if not text:
        return text
    out = text.strip()
    out = re.sub(
        rf"^(Özetle[;,]\s+)(?:siz|sizin)\s+(?:i[şs]aretledi[ğg]iniz|seçti[ğg]iniz|seçmi[şs]\s+oldu[ğg]unuz|verdi[ğg]iniz)\s+(?:{QC_DOT}|[^.,]+?)\s*,?\s*",
        r"\1",
        out,
        flags=re.IGNORECASE | re.UNICODE,
    )
    out = re.sub(
        rf"^Sizin\s+(?:i[şs]aretledi[ğg]iniz|seçti[ğg]iniz|seçmi[şs]\s+oldu[ğg]unuz|verdi[ğg]iniz)\s+(?:{QC_DOT}\s+)?[^.]*?\.\s*",
        "",
        out,
        flags=re.IGNORECASE | re.UNICODE,
    )
    for pat, repl in PHASE_A_FIXES:
        out = pat.sub(repl, out)
    for pat, repl in PHASE_B_FIXES:
        out = pat.sub(repl, out)
    for pat, repl in PHASE_C_FIXES:
        out = pat.sub(repl, out)
    for pat, repl in PHASE_D_FIXES:
        out = pat.sub(repl, out)
    return out.strip()


# Apply and audit
with open(r'C:\Users\701693\turk_patent\education\sorular.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

fired_exp = 0
residual_exp = []
residual_sum = []

for i, q in enumerate(data):
    right = next(o['text'] for o in q['options'] if o['status'] == 'Right answer')
    raw = q['detailedExplanation']['assistantResponse']
    orig_exp = raw.get('coreExplanation') or ''
    orig_sum = raw.get('summary') or ''
    new_exp, fired = clean_explanation(orig_exp, right)
    new_sum = clean_summary(orig_sum, right) if orig_sum else ''

    if fired:
        fired_exp += 1

    for probe in PERSONALIZATION_PROBES:
        if probe in new_exp:
            residual_exp.append((i, probe, new_exp))
            break
    if new_sum:
        for probe in PERSONALIZATION_PROBES:
            if probe in new_sum:
                residual_sum.append((i, probe, new_sum))
                break

print(f"=== V7 AUDIT ===")
print(f"Opener fired:                            {fired_exp}/{len(data)} ({fired_exp*100//len(data)}%)")
print(f"Residual personalization in explanation: {len(residual_exp)}/{len(data)}")
print(f"Residual personalization in summary:     {len(residual_sum)}")

print(f"\n=== Residual EXPLANATION (first 10, context only) ===")
for idx, probe, snippet in residual_exp[:10]:
    pos = snippet.find(probe)
    ctx_start = max(0, pos - 50)
    ctx_end = min(len(snippet), pos + len(probe) + 130)
    print(f"[{idx}] probe='{probe}': ...{snippet[ctx_start:ctx_end]}...")
    print()

print(f"\n=== Residual SUMMARY (first 5) ===")
for idx, probe, snippet in residual_sum[:5]:
    pos = snippet.find(probe)
    ctx_start = max(0, pos - 50)
    ctx_end = min(len(snippet), pos + len(probe) + 130)
    print(f"[{idx}] probe='{probe}': ...{snippet[ctx_start:ctx_end]}...")
    print()
