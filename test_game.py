import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from curriculum import UNITS, GAME_MODES
from game import generate, check, public_question

def test_all_game_modes_generate_graded_tasks():
    assert len(GAME_MODES) == 12
    for mode, _, _ in GAME_MODES:
        questions=generate('game',mode=mode)
        assert len(questions) >= 6
        for q in questions:
            assert check(q,q['correct'])
            assert 'correct' not in public_question(q)
            assert 'explanation' not in public_question(q)

def test_all_lessons_have_ten_playable_tasks():
    assert len(UNITS)==12
    for i in range(len(UNITS)):
        questions=generate('lesson',lesson_id=i)
        assert len(questions)==10
        assert all(check(q,q['correct']) for q in questions)

def test_wrong_answers_do_not_pass():
    for m in ['translate','write','fix','sentence','truth','missing']:
        q=generate('game',mode=m)[0]
        assert not check(q,'this is an intentionally wrong answer')

def test_pairs_reject_missing_matches():
    q=generate('game',mode='pairs')[0]
    assert not check(q,{})
