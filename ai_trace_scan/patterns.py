"""Pattern constants for AI trace detection."""

from __future__ import annotations

TRAILER_PATTERNS: list[tuple[str, str]] = [
    (r"Co-authored-by:.*(?:Copilot|copilot|GitHub\sCopilot)", "Co-authored-by Copilot trailer"),
    (r"Co-authored-by:.*(?:Claude|Anthropic)", "Co-authored-by Claude trailer"),
    (r"Co-authored-by:.*(?:GPT|OpenAI|ChatGPT)", "Co-authored-by GPT/OpenAI trailer"),
    (r"Co-authored-by:.*(?:Cursor|Aider|Codeium|Tabnine|Gemini)", "Co-authored-by AI tool trailer"),
]

COMMIT_MSG_PATTERNS: list[tuple[str, str]] = [
    (
        r"\b(?:as an AI|as a language model|per your instructions)\b",
        "Agentic language in commit message",
    ),
    (r"\breview:\s*Copilot\b", "Copilot review marker"),
    (
        r"\bgenerated (?:by|with|using) (?:Copilot|Claude|GPT|AI|Cursor|Aider|Gemini)\b",
        "AI generation attribution",
    ),
    (
        r"\b(?:copilot|claude|cursor|aider)\s+(?:suggested|generated|wrote|created)\b",
        "AI tool attribution",
    ),
    (
        r"^[\U0001f300-\U0001faff\u2600-\u27bf\u2b50]",
        "Emoji prefix in commit subject (common AI convention)",
    ),
    # AI-to-user conversational patterns (second person)
    (
        r"\b(?:as you requested|you asked (?:for|me to)|per your request|as you (?:mentioned|suggested|wanted))\b",
        "AI-to-user language in commit message",
    ),
    (
        r"\b(?:based on your (?:feedback|request|instructions|requirements))\b",
        "AI-to-user reference in commit message",
    ),
    (
        r"\blet me know if you(?:'d| would) like\b",
        "AI-to-user conversational phrase in commit message",
    ),
    # AI self-referencing first person
    (
        r"\bI(?:'ve| have) (?:implemented|added|refactored|updated|created|fixed|removed|replaced|modified|restructured|reorganized)\b",
        "AI first-person voice in commit message",
    ),
    (
        r"\b(?:here(?:'s| is) (?:the|what|my)|I(?:'ll| will) (?:create|update|add|fix|refactor|implement))\b",
        "AI first-person voice in commit message",
    ),
]

BOT_AUTHOR_PATTERNS: list[tuple[str, str]] = [
    (r"copilot\[bot\]", "Copilot bot author"),
    (r"github-actions\[bot\].*copilot", "GitHub Actions Copilot bot"),
    (r"\+Copilot@users\.noreply\.github\.com", "Copilot noreply email"),
    (r"devin\[bot\]", "Devin bot author"),
    (r"sweep\[bot\]", "Sweep bot author"),
]

BRANCH_PATTERNS: list[str] = [
    r"^copilot/",
    r"^claude/",
    r"^ai[-/]",
    r"^cursor[-/]",
    r"^aider[-/]",
    r"^gemini[-/]",
    r"^devin[-/]",
    r"^sweep[-/]",
]

AGENT_CONFIG_FILES: list[str] = [
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".cursorrules",
    ".cursorignore",
    ".aider.conf.yml",
    ".aider.input.history",
    ".aider.chat.history.md",
    ".aider.tags.cache.v3",
    ".github/copilot-instructions.md",
    ".github/copilot-review-instructions.md",
]

AGENT_CONFIG_GLOBS: list[str] = [
    ".cursor/",
    ".aider*",
    ".copilot/",
]

DIFF_PATTERNS: list[tuple[str, str]] = [
    (
        r"\b(?:this (?:code|function|class|module) was (?:generated|written|created) (?:by|with|using) (?:ai|copilot|claude|gpt|chatgpt|cursor|aider|gemini))\b",
        "AI authorship claim in code",
    ),
    (
        r"\bTODO\s*:?\s*(?:copilot|claude|ai|gpt)\b",
        "AI-referencing TODO comment",
    ),
    (
        r"\b(?:as (?:an ai|a language model)|I (?:don't|cannot|can't) (?:actually|really))\b",
        "AI instruction remnant in code",
    ),
    # AI-to-user conversational patterns left in code
    (
        r"\b(?:as you requested|you asked (?:for|me to)|per your request|per your instructions)\b",
        "AI-to-user language in code",
    ),
    (
        r"\blet me know if you(?:'d| would) like\b",
        "AI-to-user conversational phrase in code",
    ),
    (
        r"\bI(?:'ve| have) (?:implemented|added|refactored|updated|created|fixed) (?:this|the|a)\b",
        "AI first-person voice in code",
    ),
]

PROSE_PATTERNS: list[tuple[str, str]] = [
    (
        r"\b(?:written|authored|drafted|created) (?:by|with|using) (?:ai|copilot|claude|gpt|chatgpt|cursor|aider|gemini|anthropic|openai)\b",
        "AI authorship attribution in prose",
    ),
    (
        r"\bthis (?:document|page|guide|readme|file) (?:was |is )?(?:generated|created|written|produced) (?:by|with|using) (?:ai|copilot|claude|gpt|chatgpt|cursor|aider|gemini)\b",
        "AI-generated document attribution",
    ),
    (
        r"\b(?:powered by|built with|assisted by) (?:copilot|claude|gpt|chatgpt|cursor|aider|gemini|anthropic|openai)\b",
        "AI tool attribution in prose",
    ),
    (
        r"\bgenerated (?:by|with|using) (?:copilot|claude|gpt|chatgpt|ai|cursor|aider|gemini)\b",
        "AI generation attribution in prose",
    ),
]

WORKFLOW_PATTERNS: list[tuple[str, str]] = [
    (
        r"\buses:\s*.*(?:copilot|claude|anthropic|openai|aider|cursor|codeium|tabnine|gemini).*",
        "AI tool action in workflow",
    ),
    (
        r"\b(?:copilot|claude|aider|cursor|codeium)\b.*(?:review|suggest|generate|fix|pr-agent)",
        "AI tool invocation in workflow",
    ),
    (
        r"\bOPENAI_API_KEY\b|\bANTHROPIC_API_KEY\b|\bCLAUDE_API_KEY\b",
        "AI service API key reference in workflow",
    ),
    (
        r"\bnpx\s+@anthropic|\bpip\s+install\s+(?:anthropic|openai|aider-chat)\b",
        "AI SDK installation in workflow",
    ),
    (
        r"\buses:\s*.*(?:pr-agent|codeball|coderabbit|sourcery|sweep)\b",
        "AI code review action in workflow",
    ),
]

COMMENT_PATTERNS: list[tuple[str, str]] = [
    (
        r"\bgenerated (?:by|with|using) (?:copilot|claude|gpt|chatgpt|ai|cursor|aider|gemini)\b",
        "AI generation attribution in comment",
    ),
    (r"\bcopilot[- ]generated\b", "Copilot-generated marker"),
    (
        r"\b(?:claude|gpt-?4|gpt-?3|chatgpt)\s+(?:wrote|generated|suggested|created)\b",
        "AI tool attribution in comment",
    ),
    (r"@generated\s+by\s+(?:ai|copilot|claude)", "Generated-by annotation"),
    # AI-to-user conversational patterns in comments
    (
        r"\b(?:as you requested|you asked (?:for|me to)|per your (?:request|instructions))\b",
        "AI-to-user language in comment",
    ),
    (
        r"\blet me know if you(?:'d| would) like\b",
        "AI-to-user conversational phrase in comment",
    ),
    (
        r"\bI(?:'ve| have) (?:implemented|added|refactored|updated|created|fixed) (?:this|the|a)\b",
        "AI first-person voice in comment",
    ),
]
