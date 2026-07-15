from riskagent.providers.config import load_model_provider_config


def test_load_model_provider_config_selects_default_model(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_DASHSCOPE_KEY", "dashscope-key")
    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
providers:
  embedding:
    default: dashscope_embedding
    models:
      dashscope_embedding:
        provider: dashscope
        model: text-embedding-v4
        api_key_env: TEST_DASHSCOPE_KEY
      openai_embedding:
        provider: openai
        model: text-embedding-3-large
        api_key_env: OPENAI_API_KEY
""",
        encoding="utf-8",
    )

    config = load_model_provider_config("embedding", path=config_path)

    assert config.provider == "dashscope"
    assert config.model == "text-embedding-v4"
    assert config.api_key == "dashscope-key"


def test_load_model_provider_config_supports_env_model_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("VLLM_MODEL_KEY", "openai_vision")
    monkeypatch.setenv("OPENAI_TEST_KEY", "openai-key")
    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
providers:
  vllm:
    default: dashscope_vision
    models:
      dashscope_vision:
        provider: dashscope
        model: qwen-vl-max
        api_key_env: DASHSCOPE_API_KEY
      openai_vision:
        provider: openai
        model: gpt-4o
        api_key_env: OPENAI_TEST_KEY
        base_url: https://example.test/v1
""",
        encoding="utf-8",
    )

    config = load_model_provider_config("vllm", path=config_path)

    assert config.provider == "openai"
    assert config.model == "gpt-4o"
    assert config.api_key == "openai-key"
    assert config.base_url == "https://example.test/v1"
