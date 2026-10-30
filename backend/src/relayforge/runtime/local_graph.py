from langsmith import tracing_context


class LocalGraph:
    """Keep this local runtime from inheriting global cloud tracing opt-ins."""

    def __init__(self, compiled):
        self.compiled = compiled

    def invoke(self, *args, **kwargs):
        with tracing_context(enabled=False):
            return self.compiled.invoke(*args, **kwargs)

    def get_state(self, *args, **kwargs):
        with tracing_context(enabled=False):
            return self.compiled.get_state(*args, **kwargs)

    def get_state_history(self, *args, **kwargs):
        with tracing_context(enabled=False):
            yield from self.compiled.get_state_history(*args, **kwargs)

    def update_state(self, *args, **kwargs):
        with tracing_context(enabled=False):
            return self.compiled.update_state(*args, **kwargs)
