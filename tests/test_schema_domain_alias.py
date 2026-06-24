from app.schemas import AskRequest, ImportIncrementalRequest, SearchRequest, UpsertRequest


def test_domain_alias_person_normalized_to_personal() -> None:
    assert SearchRequest(query="q", domain="person").domain == "personal"
    assert AskRequest(question="q", domain="person").domain == "personal"
    assert ImportIncrementalRequest(directory="d", project="p", domain="person").domain == "personal"
    req = UpsertRequest(
        title="t",
        domain="person",
        project="p",
        type="fact",
        content_markdown="c",
        author="a",
    )
    assert req.domain == "personal"
