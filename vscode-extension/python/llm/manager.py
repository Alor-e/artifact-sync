import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional
from core.schemas import RefinementDecision, AnalysisResult, ChatConfig, DetailedImpactReport
from core.errors import RetryableError, NonRetryableError
from core.abstractions import ModelProviderInterface, ChatSession, AsyncChatSession

class UnifiedChatManager:
    """Vendor-agnostic chat manager with context awareness and retry logic"""
    
    def __init__(self, 
                 provider: ModelProviderInterface,
                 api_key: str,
                 tree_json: str, 
                 diffs_context: str,
                 readme_content: Optional[str] = None,
                 max_retries: int = 3):
        self.provider = provider
        self.api_key = api_key
        self.tree_json = tree_json
        self.diffs_context = diffs_context
        self.readme_content = readme_content
        self.max_retries = max_retries
        
        # Create client
        self.client = provider.create_client(api_key)
        
        # Context storage - reusable chats with global context
        self.main_chat: Optional[ChatSession] = None
        self.refinement_chat: Optional[ChatSession] = None
        self.subfolder_chats: Dict[str, ChatSession] = {}
    
    def _create_chat_with_retry(self, config: ChatConfig) -> ChatSession:
        """Create a chat session with retry logic for handling transient API errors"""
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                chat = self.provider.create_chat(self.client, config)
                return chat
                
            except RetryableError as e:
                last_exception = e
                wait_time = self.provider.get_error_wait_time(e, attempt)
                print(f"[WARN] API error during chat creation (attempt {attempt + 1}/{self.max_retries}): {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
                
            except NonRetryableError as e:
                print(f"[ERROR] A non-retryable error occurred during chat creation: {e}")
                raise
            
            except Exception as e:
                # Fallback error handling
                if self.provider.is_retryable_error(e):
                    last_exception = RetryableError(str(e))
                    wait_time = self.provider.get_error_wait_time(e, attempt)
                    print(f"[WARN] Unknown retryable error (attempt {attempt + 1}/{self.max_retries}): {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"[ERROR] Unknown non-retryable error: {e}")
                    raise NonRetryableError(str(e)) from e
        
        # If all retries fail, raise the last caught exception
        raise RuntimeError(f"Failed to create chat session after {self.max_retries} attempts.") from last_exception
    
    def _create_base_context(self) -> str:
        """Create the base context that all chats should have"""
        base = f"""
        Repository Analysis Context:
        
        Directory Tree Structure:
        ```json
        {self.tree_json}
        ```
        
        In the JSON tree, a node with "truncated": true means  "we reached the maximum depth here—there may be files or subfolders below, but we didn't include them."
        
        Global Commit Changes:
        ```text
        {self.diffs_context}
        ```
        
        """

        # If we loaded a README, give the LLM an abbreviated look
        if self.readme_content:
            base += f"""
            Repository README (truncated if large):
            ```markdown
            {self.readme_content}
            ```

            """
            
        base += f"""
        You are analyzing change impact across this repository. The diffs shown are global repository changes, not local to any subfolder.
        """
        
        return base
    
    def get_main_analysis_chat(self) -> ChatSession:
        """Get or create the main analysis chat with full context"""
        if self.main_chat is not None:
            return self.main_chat
        
        print("[CONTEXT] Creating main analysis chat")

        system_instruction = f"""
        You are a change-impact analysis agent specializing in detecting BOTH direct and indirect relationships. 
        Given the directory tree and commit diffs, identify files and folders affected by or related to the changes. 
        Rules:
        1. Any file may appear in 'sure' or 'unsure' if it's changed or downstream impacted.
        2. Folders should only be listed if their depth == max_depth.
        3. Do not include folders with depth < max_depth.
        Output JSON schema:
        {{
        sure: string[],
        unsure: [
            {{ path: string, is_dir: boolean, reason: string, needed_info: 'file_overview'|'file_metadata'|'raw_content' }}
        ]
        }}

        {self._create_base_context()}
        """
        
        config = ChatConfig(
            system_instruction=system_instruction,
            temperature=0,
            response_format="json",
            response_schema=AnalysisResult
        )

        self.main_chat = self._create_chat_with_retry(config)
        return self.main_chat
    
    def get_refinement_chat(self) -> ChatSession:
        """Get or create the refinement chat with full context"""
        if self.refinement_chat is not None:
            return self.refinement_chat
        
        print("[CONTEXT] Creating refinement chat")

        system_instruction = f"""
        You are making refinement decisions on change impact analysis.
        You have access to the full repository context and global changes.
        For each file/folder, determine if it's impacted by the changes with high confidence when possible.

        {self._create_base_context()}
        """
        
        config = ChatConfig(
            system_instruction=system_instruction,
            temperature=0,
            response_format="json",
            response_schema=RefinementDecision
        )
        
        self.refinement_chat = self._create_chat_with_retry(config)
        return self.refinement_chat
    
    def get_subfolder_chat(self, folder_path: str) -> ChatSession:
        """Get or create a subfolder-specific chat with full global context"""
        if folder_path in self.subfolder_chats:
            return self.subfolder_chats[folder_path]
        
        print(f"[CONTEXT] Creating subfolder chat for {folder_path}")

        base_context = self._create_base_context()
        subfolder_context = f"{base_context}\n\nYou will be analyzing the subfolder: {folder_path}"

        system_instruction = f"""
        You are analyzing a specific subfolder within a repository for change impact.
        You have access to the full repository context and global changes.
        Focus on identifying files within the given subfolder that are impacted by the global changes.

        {subfolder_context}
        """
        
        config = ChatConfig(
            system_instruction=system_instruction,
            temperature=0,
            response_format="json",
            response_schema=AnalysisResult
        )
        
        # Send base context with subfolder focus
        chat = self._create_chat_with_retry(config)
        
        self.subfolder_chats[folder_path] = chat
        return chat
    
    def get_reporting_chat(self) -> ChatSession:
        """Get or create the reporting chat optimized for detailed impact analysis"""
        if hasattr(self, 'reporting_chat') and self.reporting_chat is not None:
            return self.reporting_chat
        
        print("[CONTEXT] Creating reporting chat")

        system_instruction = f"""
        You are a change-impact analysis expert providing detailed diagnostic reports.
        Your role is to provide a report in three distinct steps:

        1. ANALYZE how files are impacted by changes
        2. DIAGNOSE whether fixes are needed (not all impacted files need updates)
        3. RECOMMEND specific actions (what to do to fix issues) if needed

        Always provide structured, actionable insights with clear reasoning.
        Consider both direct changes and indirect impacts through dependencies.

        You have access to the full repository context and global changes.
        Focus on identifying files within the given subfolder that are impacted by the global changes.
        Be specific about what needs to be done and why.

        {self._create_base_context()}
        """
        
        config = ChatConfig(
            system_instruction=system_instruction,
            temperature=0.1,
            response_format="json",
            response_schema=DetailedImpactReport
        )
        
        self.reporting_chat = self._create_chat_with_retry(config)
        return self.reporting_chat
    
class AsyncUnifiedChatManager(UnifiedChatManager):
    """Extended chat manager with async capabilities"""
    
    def __init__(self, *args, max_workers: int = 5, **kwargs):
        super().__init__(*args, **kwargs)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._async_cache: Dict[str, AsyncChatSession] = {}
    
    def get_async_chat(self, chat_key: str) -> AsyncChatSession:
        """Get or create an async wrapper for a chat session"""
        if chat_key in self._async_cache:
            return self._async_cache[chat_key]
        
        # Get the appropriate sync chat
        if chat_key == "main":
            sync_chat = self.get_main_analysis_chat()
        elif chat_key == "refinement":
            sync_chat = self.get_refinement_chat()
        elif chat_key.startswith("subfolder:"):
            folder_path = chat_key[10:]  # Remove "subfolder:" prefix
            sync_chat = self.get_subfolder_chat(folder_path)
        elif chat_key == "reporting":
            sync_chat = self.get_reporting_chat()
        else:
            raise ValueError(f"Unknown chat key: {chat_key}")
        
        # Create async wrapper
        async_chat = AsyncChatSession(sync_chat, self.executor)
        self._async_cache[chat_key] = async_chat
        return async_chat
    
    def __del__(self):
        """Cleanup executor on deletion"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=True)