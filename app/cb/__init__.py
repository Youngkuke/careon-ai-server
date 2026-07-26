"""CareOn 챗봇 검색엔진 (cb).

기존 챗봇(app/routers/chat.py, app/services/*)과 완전히 분리된 병행 구현이다.
DB는 cb 스키마만 쓰고 public 스키마를 참조하지 않는다.
"""
