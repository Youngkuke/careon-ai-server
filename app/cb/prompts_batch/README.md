# prompts_batch — 배치 전용 프롬프트

여기 있는 프롬프트는 **오프라인 배치 스크립트만** 쓴다. 실시간 챗봇 답변
경로는 이 디렉토리를 절대 읽지 않는다.

## 왜 디렉토리를 나눴나

`app/cb/prompts/`의 프롬프트(converse, narrow, explain, intent_extract, intake)는
**"원문에 없는 것은 말하지 않는다"** 는 원칙 위에 서 있다. 사용자가 챗봇 답변을
읽고 주민센터에 가기 때문이다.

이 배치는 그 반대다. 데모 화면을 채우려고 **근거가 얕아도 추정을 허용한다.**
두 방침이 한 디렉토리에 섞이면, 언젠가 누군가 배치 프롬프트의 관대한 문구를
실시간 프롬프트로 복사해 온다. 그래서 파일 시스템에서부터 갈라놓는다.

## 경계가 지켜지는 방식

`app/cb/prompts.py`의 `load()`는 `app/cb/prompts/`로 경로가 고정돼 있고,
호출부 5곳 모두 literal 이름만 넘긴다:

| 호출부 | 이름 |
| --- | --- |
| `app/cb/explain.py:46` | `explain` |
| `app/cb/nodes.py:171` | `intent_extract` |
| `app/cb/nodes.py:446` | `intake` |
| `app/cb/nodes.py:528` | `narrow` |
| `app/cb/nodes.py:602` | `converse` |

배치 스크립트는 `prompts.load()`를 쓰지 않고 자기 로더로 이 디렉토리를 읽는다
(`scripts/backfill_required_documents.py`의 `load_batch_prompt()`).

경계는 `scripts/check_prompt_isolation.py`가 검사한다. 실시간 코드가
`prompts_batch`를 import하거나 `load()`에 경로 문자열을 넘기면 실패한다.

## 규칙

- 이 디렉토리의 프롬프트를 `app/cb/prompts/`로 옮기거나 복사하지 않는다.
- 여기서 만든 값을 실시간 답변이 읽지 않는다. 현재 대상 컬럼
  (`required_documents_ai`)은 `app/cb/search.py`·`nodes.py`·`explain.py`
  어디에서도 SELECT하지 않는다.
- 이 디렉토리의 결과물은 화면에서 'AI 추정'임이 드러나야 한다.
