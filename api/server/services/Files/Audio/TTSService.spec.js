jest.mock('axios');
jest.mock('@librechat/data-schemas', () => ({ logger: { warn: jest.fn(), error: jest.fn() } }));
jest.mock('@librechat/api', () => ({
  genAzureEndpoint: jest.fn(),
  logAxiosError: jest.fn(),
  applyAxiosProxyConfig: jest.fn(),
  applySSRFSafeAgentIfDirect: jest.fn(),
  resolveConfigSecret: jest.fn(),
}));
jest.mock('librechat-data-provider', () => ({
  extractEnvVariable: jest.fn((value) => value),
  /* Real selection logic: these tests are about which provider a schema resolves to. */
  listConfiguredSpeechProviders:
    jest.requireActual('librechat-data-provider').listConfiguredSpeechProviders,
  TTSProviders: {
    OPENAI: 'openai',
    AZURE_OPENAI: 'azureOpenAI',
    ELEVENLABS: 'elevenlabs',
    LOCALAI: 'localai',
  },
}));
jest.mock('./streamAudio', () => ({
  getRandomVoiceId: jest.fn(),
  createChunkProcessor: jest.fn(),
  splitTextIntoChunks: jest.fn(),
}));
jest.mock('~/server/services/Config', () => ({ getAppConfig: jest.fn() }));

const { logAxiosError, resolveConfigSecret } = require('@librechat/api');
const axios = require('axios');
const { createChunkProcessor, splitTextIntoChunks } = require('./streamAudio');
const { TTSService, getProvider } = require('./TTSService');

describe('TTSService provider header construction with an undecryptable apiKey', () => {
  let service;

  beforeEach(() => {
    service = new TTSService();
    resolveConfigSecret.mockReset();
  });

  it('omits the Authorization header for openAIProvider instead of sending "Bearer undefined"', () => {
    resolveConfigSecret.mockReturnValue(undefined);
    const [, , headers] = service.openAIProvider(
      { apiKey: 'v3:corrupted', voices: [] },
      'hi',
      'alloy',
    );
    expect(headers).not.toHaveProperty('Authorization');
  });

  it('sets the Authorization header normally when the key resolves', () => {
    resolveConfigSecret.mockReturnValue('sk-real-key');
    const [, , headers] = service.openAIProvider({ apiKey: 'v3:ok', voices: [] }, 'hi', 'alloy');
    expect(headers.Authorization).toBe('Bearer sk-real-key');
  });

  it('omits the xi-api-key header for elevenLabsProvider instead of sending "undefined"', () => {
    resolveConfigSecret.mockReturnValue(undefined);
    const [, , headers] = service.elevenLabsProvider(
      { apiKey: 'v3:corrupted', voices: ['ALL'] },
      'hi',
      'voice1',
      false,
    );
    expect(headers).not.toHaveProperty('xi-api-key');
  });

  it('omits the Authorization header for localAIProvider instead of sending "Bearer undefined"', () => {
    resolveConfigSecret.mockReturnValue(undefined);
    const [, , headers] = service.localAIProvider(
      { apiKey: 'v3:corrupted', voices: [] },
      'hi',
      'voice1',
    );
    expect(headers).not.toHaveProperty('Authorization');
  });
});

describe('TTSService getProvider detection', () => {
  const buildConfig = (tts) => ({ speech: { tts } });

  it('resolves exactly one provider when allowedAddresses is set alongside it', async () => {
    const provider = await getProvider(
      buildConfig({
        allowedAddresses: ['localhost:11434'],
        localai: { url: 'http://localhost:11434/tts', apiKey: 'sk' },
      }),
    );
    expect(provider).toBe('localai');
  });

  it('reports "No provider is set" when only allowedAddresses is present', async () => {
    await expect(
      getProvider(buildConfig({ allowedAddresses: ['localhost:11434'] })),
    ).rejects.toThrow('No provider is set');
  });

  it('reports "Multiple providers" when two providers are set even with allowedAddresses', async () => {
    await expect(
      getProvider(
        buildConfig({
          allowedAddresses: ['localhost:11434'],
          openai: { url: 'http://localhost:11434', apiKey: 'sk' },
          localai: { url: 'http://localhost:11434/tts', apiKey: 'sk' },
        }),
      ),
    ).rejects.toThrow('Multiple providers are set');
  });
});

describe('TTSService error logging', () => {
  beforeEach(() => {
    logAxiosError.mockClear();
    createChunkProcessor.mockReset();
    splitTextIntoChunks.mockReset();
    axios.post.mockReset();
  });

  it('redacts speech text from provider request errors and rethrows a safe error', async () => {
    const service = new TTSService();
    const canary = 'PRIVATE_MEETING_TRANSCRIPT_CANARY';
    axios.post.mockRejectedValue(new Error(`${canary} request config body`));
    resolveConfigSecret.mockReturnValue(undefined);

    await expect(
      service.ttsRequest('openai', { voices: [] }, { input: canary, voice: 'alloy' }, []),
    ).rejects.toThrow('TTS provider request failed');

    expect(logAxiosError).toHaveBeenCalledTimes(1);
    const loggedError = logAxiosError.mock.calls[0][0];
    expect(JSON.stringify(loggedError)).not.toContain(canary);
    expect(loggedError.error.message).toBe('TTS provider request failed');
  });

  it('redacts streamed speech text and provider error details from logs', async () => {
    const service = new TTSService();
    const canary = 'PRIVATE_MEETING_TRANSCRIPT_CANARY';
    createChunkProcessor.mockReturnValue(
      jest.fn().mockResolvedValue([{ text: canary, isFinished: true }]),
    );
    jest.spyOn(service, 'getProvider').mockReturnValue('openai');
    jest.spyOn(service, 'getVoice').mockResolvedValue('alloy');
    jest
      .spyOn(service, 'ttsRequest')
      .mockRejectedValue(new Error(`${canary} provider request body`));

    const req = {
      config: { speech: { tts: { openai: { voices: ['alloy'] } } } },
      user: { id: 'user-1' },
      body: { messageId: 'message-1' },
      on: jest.fn(),
    };
    const res = {
      headersSent: false,
      setHeader: jest.fn(),
      status: jest.fn().mockReturnThis(),
      end: jest.fn(),
    };

    await service.streamAudio(req, res);

    expect(logAxiosError).toHaveBeenCalledTimes(1);
    const loggedError = logAxiosError.mock.calls[0][0];
    expect(JSON.stringify(loggedError)).not.toContain(canary);
    expect(loggedError.message).toBe('[TTS] Error processing audio stream update');
    expect(loggedError.error.message).toBe('TTS chunk generation failed');
    expect(res.status).toHaveBeenCalledWith(500);
  });

  it('redacts manual multi-chunk speech text from provider and chunk logs', async () => {
    const service = new TTSService();
    const canary = 'PRIVATE_MEETING_TRANSCRIPT_CANARY';
    splitTextIntoChunks.mockReturnValue([{ text: canary, isFinished: true }]);
    axios.post.mockRejectedValue(new Error(`${canary} provider request body`));
    resolveConfigSecret.mockReturnValue(undefined);
    jest.spyOn(service, 'getProvider').mockReturnValue('openai');
    jest.spyOn(service, 'getVoice').mockResolvedValue('alloy');

    const req = {
      config: { speech: { tts: { openai: { voices: ['alloy'] } } } },
      user: { id: 'user-1' },
      body: { input: canary.repeat(130), voice: 'alloy' },
    };
    const res = {
      headersSent: false,
      setHeader: jest.fn(),
      status: jest.fn().mockReturnThis(),
      send: jest.fn(),
      end: jest.fn(),
    };

    await service.processTextToSpeech(req, res);

    expect(logAxiosError).toHaveBeenCalledTimes(2);
    expect(JSON.stringify(logAxiosError.mock.calls)).not.toContain(canary);
    expect(res.status).toHaveBeenCalledWith(500);
  });
});
