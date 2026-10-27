/**
 * Keep agent action binding out of Jest worker boot while ignoring the retired
 * JURISTAI_ENSURE_AGENT_ACTION override. The persisted action schema must be
 * reconciled from the current OpenAPI spec on every real server boot.
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {boolean}
 */
function shouldBindJuristaiAgentAction(env = process.env) {
  return env.JEST_WORKER_ID === undefined;
}

module.exports = { shouldBindJuristaiAgentAction };
