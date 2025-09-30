import os
import asyncio
from typing import List, Tuple, Optional, Any, Dict

from llm.manager import AsyncUnifiedChatManager
from utils.reporting_normalizer import coerce_detailed_report_payload


def report_simple(final_sure: List[str]):
    print("\n=== Final Results ===")
    print(f"Files that ARE impacted ({len(final_sure)}):")
    for f in sorted(final_sure):
        print(f"  - {f}")


async def report_detailed_async(final_sure: List[str],
                               chat_manager: AsyncUnifiedChatManager,
                               root_path: str,
                               evaluation_tracker: Optional[Any] = None) -> List[Dict[str, Any]]:
    print("\n=== Detailed Impact Analysis ===")
    collected_reports: List[Dict[str, Any]] = []

    async def analyze_single_file(path: str) -> Tuple[str, str]:
        full = os.path.join(root_path, path)
        loop = asyncio.get_event_loop()

        def read_file():
            try:
                with open(full, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if len(content) > 10000:
                        content = content[:10000] + "\n... [truncated]"
                    return content
            except Exception:
                return "<could not read file>"

        text = await loop.run_in_executor(chat_manager.executor, read_file)

        prompt = f"""
        You are a change-impact expert analyzing a repository. Given the repository tree + commit diffs (in context), provide a comprehensive analysis for **{path}**.

        File content:
        ```text
        {text}
        ```

        Provide your analysis in the following JSON structure:

        1. **Analysis** - How is this file impacted?
        - Is it directly or indirectly impacted by the commit?
        - Explain in detail how the file is impacted

        2. **Diagnosis** - Does this file need updates?
        - Sometimes files are impacted but don't need changes
        - Explanation of why updates are or aren't needed

        3. **Recommendations** - What should be done to fix it?
        - Provide at most three concise, file-specific actions (no repository-wide guidance)
        - Each action should be unique, concrete, and focused on changes to this file only
        - Avoid repeating information already covered for other files or general security advice

        The idea is to analyze what's wrong, determine if fixes are needed, and provide a blueprint for the fixes.

        Focus on being practical and actionable. If no updates are needed, explain why. If updates are needed, be specific about what should be done while keeping the response tight and non-redundant.
        """

        chat = chat_manager.get_async_chat("reporting")
        resp = await chat.send_message_async(prompt)

        parsed_payload = None
        if resp.parsed is not None:
            parsed_payload = resp.parsed.model_dump()
        else:
            parsed_payload = coerce_detailed_report_payload(resp.content, path=path)

        if evaluation_tracker:
            evaluation_tracker.add_tokens('reporting', resp.usage)
            evaluation_tracker.add_recommendation(path, parsed_payload, resp.content)

        collected_reports.append({
            'path': path,
            'content': resp.content,
            'parsed': parsed_payload
        })

        return path, resp.content

    tasks = [analyze_single_file(path) for path in sorted(final_sure)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            print(f"\n— Error analyzing file: {result} —")
            continue

        path, explanation = result
        print(f"\n=== {path} ===\n{explanation}\n")

    return collected_reports
