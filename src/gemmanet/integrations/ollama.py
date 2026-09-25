import json
import logging

import httpx

from gemmanet.sdk.models import Completion

logger = logging.getLogger('gemmanet.integrations.ollama')


class OllamaHandler:
    '''Ready-made handler that connects Ollama to GemmaNet.

    Usage:
        from gemmanet import Node
        from gemmanet.integrations.ollama import OllamaHandler

        handler = OllamaHandler(model='gemma2:9b')
        node = Node(name='my-node', capabilities=['chat'], api_key='gn_...')
        node.register_handler('chat', handler)
        node.start()

    Requests from the OpenAI-compatible endpoint carry the whole
    conversation in ``params['messages']``, which is sent to Ollama as-is.
    Callers cannot pick a different local model unless
    ``allow_model_override=True``.
    '''

    # Specialized handlers build their own prompt from `content` and ignore
    # any chat history.
    use_messages = True

    def __init__(self, model: str = 'gemma2:9b',
                 ollama_url: str = 'http://localhost:11434',
                 system_prompt: str | None = None,
                 temperature: float = 0.7,
                 timeout: float = 120.0,
                 allow_model_override: bool = False):
        self.model = model
        self.ollama_url = ollama_url.rstrip('/')
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.timeout = timeout
        self.allow_model_override = allow_model_override
        self._client = httpx.Client(timeout=timeout)

    def prepare(self, content: str, params: dict) -> str:
        '''Turn the task content into the user prompt (subclass hook).'''
        return content

    def _build_request(self, content: str, params: dict, stream: bool) -> dict:
        prompt = self.prepare(content, params)
        sys_prompt = params.get('system_prompt', self.system_prompt)

        history = params.get('messages') if self.use_messages else None
        if isinstance(history, list) and history:
            messages = [{'role': str(m.get('role', 'user')), 'content': str(m.get('content', ''))}
                        for m in history if isinstance(m, dict)]
            if sys_prompt and not any(m['role'] == 'system' for m in messages):
                messages.insert(0, {'role': 'system', 'content': sys_prompt})
        else:
            messages = []
            if sys_prompt:
                messages.append({'role': 'system', 'content': sys_prompt})
            messages.append({'role': 'user', 'content': prompt})

        model = self.model
        if self.allow_model_override and params.get('model'):
            model = str(params['model'])

        options = {'temperature': params.get('temperature', self.temperature)}
        if params.get('max_tokens'):
            options['num_predict'] = int(params['max_tokens'])
        return {'model': model, 'messages': messages, 'stream': stream, 'options': options}

    @staticmethod
    def _usage(data: dict) -> dict | None:
        if 'prompt_eval_count' not in data and 'eval_count' not in data:
            return None
        return {'prompt_tokens': int(data.get('prompt_eval_count', 0)),
                'completion_tokens': int(data.get('eval_count', 0))}

    def _raise_connect_error(self, error: Exception):
        logger.error(f'Cannot connect to Ollama at {self.ollama_url}. Is Ollama running?')
        raise RuntimeError(f'Cannot connect to Ollama at {self.ollama_url}') from error

    def __call__(self, content: str, **params) -> str:
        '''Called by GemmaNet when a task is assigned to this node.

        Returns the answer text (a ``Completion`` carrying token usage).
        Raises on failure so the task is reported as failed.
        '''
        request = self._build_request(content, params, stream=False)
        try:
            response = self._client.post(f'{self.ollama_url}/api/chat', json=request)
            response.raise_for_status()
        except httpx.ConnectError as e:
            self._raise_connect_error(e)
        data = response.json()
        result = data.get('message', {}).get('content', '')
        logger.info(f'Ollama response: {len(result)} chars, model={request["model"]}')
        return Completion(result, usage=self._usage(data))

    def stream(self, content: str, **params):
        '''Generator variant used when the caller asked for streaming.

        Yields text pieces as Ollama produces them; returns token usage.
        '''
        request = self._build_request(content, params, stream=True)
        try:
            with self._client.stream('POST', f'{self.ollama_url}/api/chat',
                                     json=request) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    piece = data.get('message', {}).get('content', '')
                    if piece:
                        yield piece
                    if data.get('done'):
                        return self._usage(data)
        except httpx.ConnectError as e:
            self._raise_connect_error(e)
        return None

    def check_connection(self) -> bool:
        '''Check if Ollama is reachable and the model is available.'''
        try:
            resp = self._client.get(f'{self.ollama_url}/api/tags')
            resp.raise_for_status()
            models = [m['name'] for m in resp.json().get('models', [])]
            if self.model in models or any(self.model in m for m in models):
                logger.info(f'Ollama OK: model {self.model} available')
                return True
            logger.warning(f'Model {self.model} not found. Available: {models}')
            return False
        except Exception as e:
            logger.error(f'Ollama connection check failed: {e}')
            return False

    def list_models(self) -> list[str]:
        '''List available models in the local Ollama instance.'''
        try:
            resp = self._client.get(f'{self.ollama_url}/api/tags')
            resp.raise_for_status()
            return [m['name'] for m in resp.json().get('models', [])]
        except Exception:
            return []

    def close(self):
        self._client.close()


class OllamaTranslateHandler(OllamaHandler):
    '''Specialized handler for translation tasks.'''
    use_messages = False

    def prepare(self, content: str, params: dict) -> str:
        source_lang = params.pop('source_lang', 'en')
        target_lang = params.pop('target_lang', 'zh')
        return (f'Translate the following text from {source_lang} '
                f'to {target_lang}. Only output the translation, '
                f'nothing else.\n\n{content}')


class OllamaSummarizeHandler(OllamaHandler):
    '''Specialized handler for summarization tasks.'''
    use_messages = False

    def prepare(self, content: str, params: dict) -> str:
        max_words = params.pop('max_words', 100)
        return (f'Summarize the following text in at most {max_words} '
                f'words. Be concise and accurate.\n\n{content}')


class OllamaCodeHandler(OllamaHandler):
    '''Specialized handler for code generation tasks.'''
    use_messages = False

    def __init__(self, model: str = 'codellama:7b', **kwargs):
        super().__init__(model=model, **kwargs)

    def prepare(self, content: str, params: dict) -> str:
        language = params.pop('language', 'python')
        return (f'Write {language} code for the following task. '
                f'Only output the code, no explanations.\n\n{content}')
