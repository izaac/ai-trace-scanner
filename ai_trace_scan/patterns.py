"""Pattern constants for AI trace detection."""

from __future__ import annotations

import re

# Type alias for compiled pattern lists
CompiledPatterns = list[tuple[re.Pattern[str], str]]

# Common AI tool name alternation — reused across multiple pattern lists.
_AI_TOOLS = (
    r"(?:copilot|claude|gpt|chatgpt|ai|cursor|aider|gemini|windsurf|cody"
    r"|amazon[\s-]?q|codewhisperer|continue|replit|bolt|lovable|v0)"
)
_AI_TOOLS_PROPER = (
    r"(?:Copilot|Claude|GPT|AI|Cursor|Aider|Gemini|Windsurf|Cody"
    r"|Amazon[\s-]?Q|CodeWhisperer|Continue|Replit|Bolt|Lovable|v0)"
)


def _compile(raw: list[tuple[str, str]]) -> CompiledPatterns:
    """Pre-compile a list of (regex_str, label) pairs."""
    return [(re.compile(p, re.IGNORECASE), label) for p, label in raw]


def _with_context(patterns: list[tuple[str, str]], context: str) -> list[tuple[str, str]]:
    """Append a context suffix (e.g. 'in code') to each label."""
    return [(regex, f"{label} {context}") for regex, label in patterns]


# ---------------------------------------------------------------------------
# Shared conversational patterns — identical regexes used across categories.
# Only the label suffix differs (added via _with_context).
# ---------------------------------------------------------------------------

_CONVERSATIONAL = [
    (
        r"\blet me know if you(?:'d| would) like\b",
        "AI-to-user conversational phrase",
    ),
    (
        r"\b(?:I apologize for the (?:oversight|confusion|error)|sorry for the (?:confusion|mistake)|you are correct,? I(?: will|'ll) (?:update|fix))\b",
        "AI apology/correction language",
    ),
    (
        r"\b(?:here(?:'s| is) the (?:updated |complete )?(?:code|implementation|script)|below is the (?:updated )?(?:code|implementation|script))\b",
        "AI code block introduction",
    ),
    (
        r"\b(?:let(?:'s| us) break this down|here is a step-by-step)\b",
        "AI conversational explanation",
    ),
    (
        r"\b(?:hope this helps!?|happy coding!?|let me know if you have any (?:other|more) questions)\b",
        "AI conversational sign-off",
    ),
    (
        r"\b(?:feel free to (?:ask|reach out|let me know)|don't hesitate to (?:ask|reach out))\b",
        "AI conversational filler",
    ),
    (
        r"\b(?:I(?:'d| would) (?:recommend|suggest) (?:using|adding|creating|implementing))\b",
        "AI recommendation phrasing",
    ),
    (
        r"\b(?:(?:great|good|excellent) (?:question|point|catch|observation)!?)\b",
        "AI praise/affirmation",
    ),
]

# ---------------------------------------------------------------------------
# Category-specific patterns
# ---------------------------------------------------------------------------

TRAILER_PATTERNS: CompiledPatterns = _compile(
    [
        (r"Co-authored-by:.*(?:Copilot|copilot|GitHub\sCopilot)", "Co-authored-by Copilot trailer"),
        (r"Co-authored-by:.*(?:Claude|Anthropic)", "Co-authored-by Claude trailer"),
        (r"Co-authored-by:.*(?:GPT|OpenAI|ChatGPT)", "Co-authored-by GPT/OpenAI trailer"),
        (
            r"Co-authored-by:.*(?:Cursor|Aider|Codeium|Tabnine|Gemini|Windsurf|Cody|Amazon[\s-]?Q|Bolt|Lovable)",
            "Co-authored-by AI tool trailer",
        ),
        (
            r"Signed-off-by:.*\[bot\]",
            "Bot Signed-off-by trailer",
        ),
        (
            r"Generated-by:\s*" + _AI_TOOLS,
            "Generated-by AI trailer",
        ),
    ]
)

COMMIT_MSG_PATTERNS: CompiledPatterns = _compile(
    [
        (
            r"\b(?:as an AI|as a language model|per your instructions)\b",
            "Agentic language in commit message",
        ),
        (r"\breview:\s*Copilot\b", "Copilot review marker"),
        (
            r"\bgenerated (?:by|with|using) " + _AI_TOOLS_PROPER + r"\b",
            "AI generation attribution",
        ),
        (
            r"\b(?:copilot|claude|cursor|aider|windsurf|cody|gemini|amazon[\s-]?q)\s+(?:suggested|generated|wrote|created)\b",
            "AI tool attribution",
        ),
        (
            r"^[\U0001f300-\U0001faff\u2600-\u27bf\u2b50]",
            "Emoji prefix in commit subject (common AI convention)",
        ),
        (
            r"\b(?:as you requested|you asked (?:for|me to)|per your request|as you (?:mentioned|suggested|wanted))\b",
            "AI-to-user language in commit message",
        ),
        (
            r"\b(?:based on your (?:feedback|request|instructions|requirements))\b",
            "AI-to-user reference in commit message",
        ),
        (
            r"\bI(?:'ve| have) (?:implemented|added|refactored|updated|created|fixed|removed|replaced|modified|restructured|reorganized)\b",
            "AI first-person voice in commit message",
        ),
        (
            r"\b(?:here(?:'s| is) (?:the|what|my)|I(?:'ll| will) (?:create|update|add|fix|refactor|implement))\b",
            "AI first-person voice in commit message",
        ),
        (
            r"^(?:Certainly!?|Of course,?|Sure thing!?|Yes, I can help(?: with that)?!?|Absolutely[,!])\s",
            "AI conversational affirmation in commit message",
        ),
        # Markdown formatting leaked into commit messages
        (
            r"^```",
            "Markdown code fence in commit message",
        ),
        (
            r"^#{1,3}\s+(?:Changes|Summary|Overview|Description|What|Why|How)\b",
            "Markdown heading in commit message",
        ),
        (
            r"^(?:Changes(?: made)?|Summary of changes|What was done|Modifications):\s*$",
            "AI-style structured commit header",
        ),
        # AI model version references
        (
            r"\b(?:gpt-?4o?|gpt-?3\.5|o[134]-(?:preview|mini)|claude-(?:3\.5|3|opus|sonnet|haiku)|gemini-(?:pro|ultra|flash|1\.5|2\.0))\b",
            "AI model version reference in commit message",
        ),
        *_with_context(_CONVERSATIONAL, "in commit message"),
    ]
)

BOT_AUTHOR_PATTERNS: CompiledPatterns = _compile(
    [
        (r"copilot\[bot\]", "Copilot bot author"),
        (r"github-actions\[bot\].*copilot", "GitHub Actions Copilot bot"),
        (r"\+Copilot@users\.noreply\.github\.com", "Copilot noreply email"),
        (r"devin\[bot\]", "Devin bot author"),
        (r"sweep\[bot\]", "Sweep bot author"),
        (r"amazon-q\[bot\]", "Amazon Q bot author"),
        (r"cody\[bot\]", "Cody bot author"),
        (r"windsurf\[bot\]", "Windsurf bot author"),
        (r"bolt\[bot\]", "Bolt bot author"),
        (r"lovable\[bot\]", "Lovable bot author"),
        (r"replit\[bot\]", "Replit bot author"),
        (r"coderabbit\[bot\]", "CodeRabbit bot author"),
        (r"sourcery\[bot\]", "Sourcery bot author"),
        (r"pr-agent\[bot\]", "PR-Agent bot author"),
    ]
)

BRANCH_PATTERNS: list[str] = [
    r"^copilot/",
    r"^copilot-workspace/",
    r"^claude/",
    r"^ai[-/]",
    r"^cursor[-/]",
    r"^aider[-/]",
    r"^gemini[-/]",
    r"^devin[-/]",
    r"^sweep[-/]",
    r"^windsurf[-/]",
    r"^cody[-/]",
    r"^amazonq[-/]",
    r"^bolt[-/]",
    r"^lovable[-/]",
    r"^replit[-/]",
    r"^v0[-/]",
]

AGENT_CONFIG_FILES: list[str] = [
    "AGENTS.md",
    "CLAUDE.md",
    "COPILOT.md",
    "GEMINI.md",
    ".cursorrules",
    ".cursorignore",
    ".windsurfrules",
    ".aider.conf.yml",
    ".aider.input.history",
    ".aider.chat.history.md",
    ".aider.tags.cache.v3",
    ".github/copilot-instructions.md",
    ".github/copilot-review-instructions.md",
    ".github/copilot-setup-steps.yml",
    "rules.md",
]

AGENT_CONFIG_GLOBS: list[str] = [
    ".cursor/",
    ".aider*",
    ".copilot/",
    ".windsurf/",
    ".cody/",
    ".continue/",
    ".amazonq/",
    ".bolt/",
    ".lovable/",
    ".v0/",
    ".replit*",
]

DIFF_PATTERNS: CompiledPatterns = _compile(
    [
        (
            r"\b(?:this (?:code|function|class|module) was (?:generated|written|created) (?:by|with|using) "
            + _AI_TOOLS
            + r")\b",
            "AI authorship claim in code",
        ),
        (
            r"\bTODO\s*:?\s*" + _AI_TOOLS + r"\b",
            "AI-referencing TODO comment",
        ),
        (
            r"\b(?:as (?:an ai|a language model)|I (?:don't|cannot|can't) (?:actually|really))\b",
            "AI instruction remnant in code",
        ),
        (
            r"\b(?:as you requested|you asked (?:for|me to)|per your (?:request|instructions))\b",
            "AI-to-user language in code",
        ),
        (
            r"\bI(?:'ve| have) (?:implemented|added|refactored|updated|created|fixed) (?:this|the|a)\b",
            "AI first-person voice in code",
        ),
        # AI model versions embedded in code
        (
            r"\b(?:gpt-?4o?|gpt-?3\.5|o[134]-(?:preview|mini)|claude-(?:3\.5|3|opus|sonnet|haiku)|gemini-(?:pro|ultra|flash|1\.5|2\.0))\b",
            "AI model version reference in code",
        ),
        # AI chat URLs leaked into source
        (
            r"https?://(?:chat\.openai\.com/share/|claude\.ai/chat/|chatgpt\.com/share/|gemini\.google\.com/share/)",
            "AI chat share URL in code",
        ),
        # HTML comments with AI attribution
        (
            r"<!--\s*(?:generated|created|written|produced)\s+(?:by|with|using)\s+" + _AI_TOOLS,
            "AI attribution in HTML comment",
        ),
        # Explicit AI-generated markers
        (
            r"(?:^|\s)@(?:ai-generated|generated|copilot)\b",
            "AI-generated annotation in code",
        ),
        # Placeholder/skeleton code that AI leaves behind
        (
            r"\b(?:implement|add|write|put)\s+(?:your\s+|the\s+)?(?:actual\s+)?(?:code|logic|implementation)\s+here\b",
            "AI placeholder/skeleton code",
        ),
        *_with_context(_CONVERSATIONAL, "in code"),
    ]
)

PROSE_PATTERNS: CompiledPatterns = _compile(
    [
        (
            r"\b(?:written|authored|drafted|created) (?:by|with|using) (?:ai|copilot|claude|gpt|chatgpt|cursor|aider|gemini|windsurf|cody|amazon[\s-]?q|anthropic|openai)\b",
            "AI authorship attribution in prose",
        ),
        (
            r"\bthis (?:document|page|guide|readme|file) (?:was |is )?(?:generated|created|written|produced) (?:by|with|using) "
            + _AI_TOOLS
            + r"\b",
            "AI-generated document attribution",
        ),
        (
            r"\b(?:powered by|built with|assisted by) (?:copilot|claude|gpt|chatgpt|cursor|aider|gemini|windsurf|cody|anthropic|openai)\b",
            "AI tool attribution in prose",
        ),
        (
            r"\bgenerated (?:by|with|using) " + _AI_TOOLS + r"\b",
            "AI generation attribution in prose",
        ),
        # AI model version references in documentation
        (
            r"\b(?:gpt-?4o?|gpt-?3\.5|o[134]-(?:preview|mini)|claude-(?:3\.5|3|opus|sonnet|haiku)|gemini-(?:pro|ultra|flash|1\.5|2\.0))\b",
            "AI model version reference in prose",
        ),
        # AI chat URLs in docs
        (
            r"https?://(?:chat\.openai\.com/share/|claude\.ai/chat/|chatgpt\.com/share/|gemini\.google\.com/share/)",
            "AI chat share URL in prose",
        ),
    ]
)

WORKFLOW_PATTERNS: CompiledPatterns = _compile(
    [
        (
            r"\buses:\s*.*(?:copilot|claude|anthropic|openai|aider|cursor|codeium|tabnine|gemini|windsurf|cody|amazon[\s-]?q).*",
            "AI tool action in workflow",
        ),
        (
            r"\b(?:copilot|claude|aider|cursor|codeium|windsurf|cody)\b.*(?:review|suggest|generate|fix|pr-agent)",
            "AI tool invocation in workflow",
        ),
        (
            r"\bOPENAI_API_KEY\b|\bANTHROPIC_API_KEY\b|\bCLAUDE_API_KEY\b|\bGEMINI_API_KEY\b|\bGOOGLE_AI_API_KEY\b",
            "AI service API key reference in workflow",
        ),
        (
            r"\bnpx\s+@anthropic|\bpip\s+install\s+(?:anthropic|openai|aider-chat|cody-ai)\b",
            "AI SDK installation in workflow",
        ),
        (
            r"\buses:\s*.*(?:pr-agent|codeball|coderabbit|sourcery|sweep)\b",
            "AI code review action in workflow",
        ),
        (
            r"\bcopilot-setup-steps\b",
            "Copilot agent setup in workflow",
        ),
    ]
)

COMMENT_PATTERNS: CompiledPatterns = _compile(
    [
        (
            r"\bgenerated (?:by|with|using) " + _AI_TOOLS + r"\b",
            "AI generation attribution in comment",
        ),
        (r"\bcopilot[- ]generated\b", "Copilot-generated marker"),
        (
            r"\b(?:claude|gpt-?4|gpt-?3|chatgpt|windsurf|cody|gemini)\s+(?:wrote|generated|suggested|created)\b",
            "AI tool attribution in comment",
        ),
        (r"@generated\s+by\s+" + _AI_TOOLS, "Generated-by annotation"),
        (
            r"\b(?:as you requested|you asked (?:for|me to)|per your (?:request|instructions))\b",
            "AI-to-user language in comment",
        ),
        (
            r"\bI(?:'ve| have) (?:implemented|added|refactored|updated|created|fixed) (?:this|the|a)\b",
            "AI first-person voice in comment",
        ),
        # AI model versions in comments
        (
            r"\b(?:gpt-?4o?|gpt-?3\.5|o[134]-(?:preview|mini)|claude-(?:3\.5|3|opus|sonnet|haiku)|gemini-(?:pro|ultra|flash|1\.5|2\.0))\b",
            "AI model version reference in comment",
        ),
        *_with_context(_CONVERSATIONAL, "in comment"),
    ]
)
