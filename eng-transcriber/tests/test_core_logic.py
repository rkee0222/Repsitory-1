"""GPU/네트워크 없이 검증 가능한 핵심 로직 테스트.

- 2분 타임스탬프 구분선이 올바른 위치에 삽입되는가
- 영어/한국어가 1:1 순서대로 조립되는가 (누락/순서변경 없음)
- 번역 결과 검증(id 매칭)이 불일치를 잡아내는가
- 청크 분할이 문장을 빠짐없이 나누는가
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Segment, TranslationUnit
from app.transcript_builder import build_lines, build_text, make_divider
from app.translate import make_chunks


def _segs(specs):
    return [Segment(id=i, start=s, end=e, text=t) for i, (s, e, t) in enumerate(specs)]


def test_dividers_at_2min_boundaries():
    segs = _segs([
        (0.0, 5.0, "A."),
        (118.0, 121.0, "B."),   # 120 경계 앞
        (121.0, 125.0, "C."),   # 120 이후
        (245.0, 250.0, "D."),   # 120, 240 둘 다 넘김
    ])
    units = [TranslationUnit(id=s.id, english=s.text, korean=f"{s.text}_ko") for s in segs]
    lines = build_lines(units, segs)

    # 기대 순서: A, [C 앞에 2:00], C 사이... 실제로는 start>=120 인 첫 문장(C) 앞에 2:00
    text = "\n\n".join(lines)
    assert make_divider(120) in text
    assert make_divider(240) in text
    # 2:00 구분선은 B(118) 다음, C(121) 앞에 위치해야 함
    order = [l for l in lines]
    i_b = next(i for i, l in enumerate(order) if l.startswith("B."))
    i_div2 = order.index(make_divider(120))
    i_c = next(i for i, l in enumerate(order) if l.startswith("C."))
    assert i_b < i_div2 < i_c, order


def test_one_to_one_pairs_preserved():
    segs = _segs([(0, 1, "Hello."), (1, 2, "Hi."), (2, 3, "Hi.")])  # 반복 문장도 유지
    units = [TranslationUnit(id=s.id, english=s.text, korean=f"번역{s.id}") for s in segs]
    lines = build_lines(units, segs)
    # 문장 개수만큼 pair 존재 (구분선 제외)
    pairs = [l for l in lines if "─" not in l]
    assert len(pairs) == 3
    for u, p in zip(units, pairs):
        en, ko = p.split("\n")
        assert en == u.english
        assert ko == u.korean


def test_english_never_modified_in_build():
    segs = _segs([(0, 1, "Teh quik brown fox.")])  # 오타가 있어도 그대로
    units = [TranslationUnit(id=0, english="Teh quik brown fox.", korean="빠른 갈색 여우.")]
    text = build_text(units, segs)
    assert "Teh quik brown fox." in text


def test_chunking_covers_all_segments():
    segs = _segs([(i, i + 1, f"S{i}.") for i in range(95)])
    chunks = make_chunks(segs, 40)
    assert len(chunks) == 3
    ids = [s.id for c in chunks for s in c.segments]
    assert ids == list(range(95))  # 순서/개수 보존


def test_translation_validation_detects_mismatch():
    from app.translate import _translate_chunk
    from app.models import Chunk
    from app.errors import TranslationStructureError

    class FakeMsg:
        def __init__(self, content):
            self.message = type("M", (), {"content": content})

    class FakeResp:
        def __init__(self, content):
            self.choices = [FakeMsg(content)]

    class FakeClient:
        def __init__(self, content):
            self._content = content
            self.chat = type("C", (), {"completions": self})()

        def create(self, **kw):
            return FakeResp(self._content)

    chunk = Chunk(index=0, segments=_segs([(0, 1, "A."), (1, 2, "B.")]))

    # 누락된 id → 예외
    bad = '{"translations":[{"id":0,"ko":"에이"}]}'
    try:
        _translate_chunk(FakeClient(bad), "m", chunk)
        assert False, "누락을 잡아야 함"
    except TranslationStructureError:
        pass

    # 정확히 1:1 → 성공
    good = '{"translations":[{"id":0,"ko":"에이"},{"id":1,"ko":"비"}]}'
    res = _translate_chunk(FakeClient(good), "m", chunk)
    assert res == {0: "에이", 1: "비"}


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
