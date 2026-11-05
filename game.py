import random
import re
from curriculum import UNITS, GAME_MODES

MODES = {mode: {"title": title, "icon": icon} for mode, title, icon in GAME_MODES}

def norm(s):
    return re.sub(r"[?!.…,;:]+", "", str(s).casefold().strip()).replace("  ", " ")

def make_question(unit, mode, rng, index):
    words = unit["words"]
    word = words[index % len(words)]
    en, ru = word
    options = lambda correct, pool, count=4: rng.sample(list(dict.fromkeys([correct] + rng.sample([v for v in pool if v != correct], min(count - 1, len(pool) - 1)))), k=min(count,len(pool)))
    q = {"mode": mode, "explanation": f"{en} — {ru}", "word": en}
    if mode in ("translate", "speed", "snake"):
        q.update(prompt=f"Как переводится «{en}»?", options=options(ru, [r for _, r in words]), correct=ru)
    elif mode == "sentence":
        text, translation = unit["sentence"]
        tokens = text.split(" ")
        ids = list(enumerate(tokens))
        rng.shuffle(ids)
        q.update(prompt=translation, tokens=[{"id":i, "text":t} for i,t in ids], correct=text, explanation=text)
    elif mode == "pairs":
        sample = rng.sample(words, min(4, len(words)))
        left = [w[0] for w in sample]; right = [w[1] for w in sample]
        rng.shuffle(left); rng.shuffle(right)
        q.update(prompt="Соедини слова с правильным переводом", left=left, right=right,
                 correct={a:b for a,b in sample}, explanation=" · ".join(f"{a} — {b}" for a,b in sample))
    elif mode == "listening":
        q.update(prompt="Прослушай и выбери слово", audioText=en,
                 options=options(en,[w[0] for w in words]), correct=en)
    elif mode == "write":
        q.update(prompt=f"Напиши по-английски: {ru}", correct=en)
    elif mode == "missing":
        template, opts, answer = unit["missing"]
        q.update(prompt=template, options=rng.sample(opts,len(opts)), correct=answer, explanation=template.replace("___",answer))
    elif mode == "truth":
        true = rng.choice([True,False])
        other = rng.choice([t for _,t in words if t != ru])
        q.update(prompt=f"{en} — {ru if true else other}", options=["Верно","Неверно"],
                 correct="Верно" if true else "Неверно", explanation=f"Правильно: {en} — {ru}")
    elif mode == "dialogue":
        prompt, opts, answer = unit["dialogue"]
        q.update(prompt=prompt, options=rng.sample(opts, len(opts)), correct=answer, explanation=answer)
    elif mode == "memory":
        q.update(prompt=f"Как по-английски «{ru}»?",
                 memorize=f"{en} — {ru}", options=options(en,[w[0] for w in words]), correct=en)
    elif mode == "fix":
        wrong, correct = unit["fix"]
        q.update(prompt=f"Исправь ошибку: {wrong}", correct=correct, explanation=correct)
    else:
        raise ValueError("Unknown mode")
    return q

def generate(kind, lesson_id=None, mode=None):
    rng = random.SystemRandom()
    if kind == "lesson":
        if lesson_id is None or not (0 <= lesson_id < len(UNITS)):
            raise ValueError("Unknown lesson")
        unit = UNITS[lesson_id]
        modes = ["translate","translate","sentence","pairs","listening","write","missing","truth","dialogue","fix"]
        return [make_question(unit, m, rng, i) for i,m in enumerate(modes)]
    if kind == "game" and mode in MODES:
        return [make_question(UNITS[rng.randrange(len(UNITS))],mode,rng,rng.randrange(8)) for _ in range(6 if mode in ("sentence","pairs","dialogue","fix") else 8)]
    raise ValueError("Unknown game")

def public_question(q):
    return {k:v for k,v in q.items() if k not in ("correct", "explanation")}

def check(q, answer):
    target = q["correct"]
    if q["mode"] == "pairs":
        return isinstance(answer,dict) and answer == target
    if q["mode"] in ("write","fix","sentence"):
        return isinstance(answer,str) and norm(answer) == norm(target)
    return answer == target
