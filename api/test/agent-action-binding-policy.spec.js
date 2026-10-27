const { shouldBindJuristaiAgentAction } = require('../../config/agent-action-binding-policy');

describe('JuristAI startup action-binding policy', () => {
  test('does not let the retired task-definition override suppress a production bind', () => {
    expect(shouldBindJuristaiAgentAction({ JURISTAI_ENSURE_AGENT_ACTION: 'false' })).toBe(true);
  });

  test('does not run the database binder in Jest workers', () => {
    expect(
      shouldBindJuristaiAgentAction({
        JEST_WORKER_ID: '1',
        JURISTAI_ENSURE_AGENT_ACTION: 'false',
      }),
    ).toBe(false);
  });
});
