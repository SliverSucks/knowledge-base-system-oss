"""P1 heading-aware chunking 单元测试。

覆盖 `BaseKnowledgeRepo._chunk_text` 与 `_split_markdown_sections` 的关键行为：
- 无标题纯文本：fallback 走原段落切分
- 单级 / 多级标题：每个 chunk 带上标题前缀作为语境
- 代码块 fence 内的 `#` 不被误识别为标题
- 超长段落滑窗切分时仍保留标题前缀
"""

from __future__ import annotations

from app.repository_base import BaseKnowledgeRepo


def _chunk(content: str, max_chars: int = 800, overlap_chars: int = 100) -> list[str]:
    return BaseKnowledgeRepo._chunk_text(content, max_chars=max_chars, overlap_chars=overlap_chars)


def test_no_heading_plain_text_returns_single_chunk_when_short():
    content = "这是一段没有任何标题的纯文本。\n\n第二段也是纯文本。"
    chunks = _chunk(content, max_chars=200)
    assert len(chunks) == 1
    assert "纯文本" in chunks[0]


def test_single_heading_prefix_attached_to_body():
    content = "# 标题一\n\n这是标题下面的正文内容。"
    chunks = _chunk(content, max_chars=200)
    assert len(chunks) == 1
    assert chunks[0].startswith("# 标题一")
    assert "正文内容" in chunks[0]


def test_multi_level_heading_path_kept_in_chunk():
    content = (
        "# 顶级标题\n\n"
        "顶级正文。\n\n"
        "## 二级标题\n\n"
        "二级正文。\n\n"
        "### 三级标题\n\n"
        "三级正文，包含关键词推送命令。"
    )
    chunks = _chunk(content, max_chars=400)
    # 至少包含一个带"### 三级标题"前缀的 chunk
    target = [c for c in chunks if "三级正文" in c]
    assert target, f"未找到三级正文 chunk: {chunks}"
    # 三级 chunk 必须带其直系或祖先标题（至少 ### 那行）
    assert "### 三级标题" in target[0]


def test_code_fence_not_treated_as_heading():
    # 代码块内的 # 是 shell 注释而非 markdown 标题，不应触发分段
    content = (
        "# 真标题\n\n"
        "下面是 shell 代码：\n\n"
        "```bash\n"
        "# 这是 shell 注释不是 markdown 标题\n"
        "sudo docker push registry.example.com/foo:latest\n"
        "```\n\n"
        "代码块之后的正文。"
    )
    chunks = _chunk(content, max_chars=800)
    # 整段都在 "# 真标题" section 下，不应被代码内的 # 拆开
    assert len(chunks) == 1
    assert "sudo docker push" in chunks[0]
    assert "代码块之后的正文" in chunks[0]


def test_long_paragraph_sliding_window_keeps_heading_prefix():
    # 单段超过 max_chars，被滑动窗口切多份，每份都应保留标题前缀
    long_body = "正文" * 300  # 600 chars
    content = f"## 长段标题\n\n{long_body}"
    chunks = _chunk(content, max_chars=200, overlap_chars=30)
    assert len(chunks) >= 2
    for c in chunks:
        assert "## 长段标题" in c, f"chunk 丢失标题前缀: {c[:80]}..."


def test_empty_content_returns_empty_list_or_self():
    assert _chunk("") in ([], [""])


def test_split_sections_skips_code_fence_headings():
    # 直接测内部函数
    content = (
        "# A\n"
        "a body\n"
        "```\n"
        "# fake heading inside code\n"
        "```\n"
        "## B\n"
        "b body\n"
    )
    sections = BaseKnowledgeRepo._split_markdown_sections(content)
    headings = [tuple(h) for h, _ in sections]
    # 应识别 # A 和 ## B 两层；代码块内 # fake heading 不应进 stack
    flat = [item for hp in headings for item in hp]
    assert any("# A" in h for h in flat)
    assert any("## B" in h for h in flat)
    assert not any("fake heading" in h for h in flat)


def test_sibling_headings_separate_sections():
    content = "## 推送到仓库\n\nsudo docker push x\n\n## 拉取镜像\n\nsudo docker pull x"
    chunks = _chunk(content, max_chars=800)
    # 两个并列二级标题应分别成段，各自带自己的标题
    push_chunks = [c for c in chunks if "docker push" in c]
    pull_chunks = [c for c in chunks if "docker pull" in c]
    assert push_chunks and "## 推送到仓库" in push_chunks[0]
    assert pull_chunks and "## 拉取镜像" in pull_chunks[0]
