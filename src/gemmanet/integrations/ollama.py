import httpx
import logging

logger = logging.getLogger('gemmanet.integrations.ollama')


class OllamaHandler:
    '''Ready-made handler that connects Ollama to GemmaNet.

    Usage:
        from gemmanet import Node
        from gemmanet.integrations.ollama import OllamaHandler

        handler = OllamaHandler(model='gemma2:9b')
        node = Node(name='my-node', capabilities=['chat'])
        node.register_handler('chat', handler)
        node.start()
    '''

    def __init__(self, model: str = 'gemma2:9b',
                 ollama_url: str = 'http://localhost:11434',
                 system_prompt: str | None = None,
                 temperature: float = 0.7,
                 timeout: float = 120.0):
        self.model = model
        self.ollama_url = ollama_url.rstrip('/')
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def __call__(self, content: str, **params) -> str:
        '''Called by GemmaNet when a task is assigned to this node.'''
        messages = []
        sys_prompt = params.get('system_prompt', self.system_prompt)
        if sys_prompt:
            messages.append({'role': 'system', 'content': sys_prompt})
        messages.append({'role': 'user', 'content': content})

        try:
            response = self._client.post(
                f'{self.ollama_url}/api/chat',
                json={
                    'model': params.get('model', self.model),
                    'messages': messages,
                    'stream': False,
                    'options': {
                        'temperature': params.get('temperature', self.temperature),
                    }
                }
            )
            response.raise_for_status()
            data = response.json()
            result = data.get('message', {}).get('content', '')
            logger.info(f'Ollama response: {len(result)} chars, '
                        f'model={self.model}')
            return result
        except httpx.ConnectError:
            logger.error(f'Cannot connect to Ollama at {self.ollama_url}. '
                         'Is Ollama running?')
            return '[Error] Cannot connect to Ollama. Make sure it is running.'
        except Exception as e:
            logger.error(f'Ollama error: {e}')
            return f'[Error] Ollama request failed: {str(e)}'

    def check_connection(self) -> bool:
        '''Check if Ollama is reachable and the model is available.'''
        try:
            resp = self._client.get(f'{self.ollama_url}/api/tags')
            resp.raise_for_status()
            models = [m['name'] for m in resp.json().get('models', [])]
            if self.model in models or any(self.model in m for m in models):
                logger.info(f'Ollama OK: model {self.model} available')
                return True
            else:
                logger.warning(f'Model {self.model} not found. '
                               f'Available: {models}')
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
    def __call__(self, content: str, source_lang: str = 'en',
                 target_lang: str = 'zh', **params) -> str:
        prompt = (f'Translate the following text from {source_lang} '
                  f'to {target_lang}. Only output the translation, '
                  f'nothing else.\n\n{content}')
        return super().__call__(prompt, **params)


class OllamaSummarizeHandler(OllamaHandler):
    '''Specialized handler for summarization tasks.'''
    def __call__(self, content: str, max_words: int = 100, **params) -> str:
        prompt = (f'Summarize the following text in at most {max_words} '
                  f'words. Be concise and accurate.\n\n{content}')
        return super().__call__(prompt, **params)


class OllamaCodeHandler(OllamaHandler):
    '''Specialized handler for code generation tasks.'''
    def __init__(self, model: str = 'codellama:7b', **kwargs):
        super().__init__(model=model, **kwargs)

    def __call__(self, content: str, language: str = 'python', **params) -> str:
        prompt = (f'Write {language} code for the following task. '
                  f'Only output the code, no explanations.\n\n{content}')
        return super().__call__(prompt, **params)
