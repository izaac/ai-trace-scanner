"""Pattern constants for AI trace detection."""

TRAILER_PATTERNS = [
    (r"Co-authored-by:.*(?:Copilot|copilot|GitHub\sCopilot)", "Co-authored-by Copilot trailer"),
    (r"Co-authored-by:.*(?:Claude|Anthropic)", "Co-authored-by Claude trailer"),
    (r"Co-authored-by:.*(?:GPT|OpenAI|ChatGPT)", "Co-authored-by GPT/OpenAI trailer"),
    (r"Co-authored-by:.*(?:Cursor|Aider|Codeium|Tabnine|Gemini)", "Co-authored-by AI tool trailer"),
]

COMMIT_MSG_PATTERNS = [
    (r"\b(?:as an AI|as a language model|per your instructions)\b", "Agentic language in commit message"),
    (r"\breview:\s*Copilot\b", "Copilot review marker"),
    (r"\bgenerated (?:by|with|using) (?:Copilot|Claude|GPT|AI|Cursor|Aider|Gemini)\b",
     "AI generation attribution"),
    (r"\b(?:copilot|claude|cursor|aider)\s+(?:suggested|generated|wrote|created)\b",
     "AI tool attribution"),
]

BOT_AUTHOR_PATTERNS = [
    (r"copilot\[bot\]", "Copilot bot author"),
    (r"github-actions\[bot\].*copilot", "GitHub Actions Copilot bot"),
    (r"\+Copilot@users\.noreply\.github\.com", "Copilot noreply email"),
    (r"devin\[bot\]", "Devin bot author"),
    (r"sweep\[bot\]", "Sweep bot author"),
]

BRANCH_PATTERNS = [
    r"^copilot/",
    r"^claude/",
    r"^ai[-/]",
    r"^cursor[-/]",
    r"^aider[-/]",
    r"^gemini[-/]",
    r"^devin[-/]",
    r"^sweep[-/]",
]

AGENT_CONFIG_FILES = [
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

AGENT_CONFIG_GLOBS = [
    ".cursor/",
    ".aider*",
    ".copilot/",
]

COMMENT_PATTERNS = [
    (r"\bgenerated (?:by|with|using) (?:copilot|claude|gpt|chatgpt|ai|cursor|aider|gemini)\b",
     "AI generation attribution in comment"),
    (r"\bcopilot[- ]generated\b", "Copilot-generated marker"),
    (r"\b(?:claude|gpt-?4|gpt-?3|chatgpt)\s+(?:wrote|generated|suggested|created)\b",
     "AI tool attribution in comment"),
    (r"@generated\s+by\s+(?:ai|copilot|claude)", "Generated-by annotation"),
]
