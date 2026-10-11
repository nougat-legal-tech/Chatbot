const fs = require('fs');
const path = require('path');

const SPEC_PATH = path.resolve(__dirname, '..', '..', 'config', 'juristai-agent-action-spec.json');

describe('CaseFlow unfiled matter tool contract', () => {
  test('documents that a new matter may omit both docketId and caseId', () => {
    const spec = JSON.parse(fs.readFileSync(SPEC_PATH, 'utf8'));
    const operation = spec.paths['/api/core/create-new-case/'].post;
    const schema = spec.components.schemas.CreateNewCase;

    expect(operation.description).toContain('new unfiled matter');
    expect(operation.description).toContain('omit both');
    expect(operation.description).toContain('mint a caseId');
    expect(schema.required).toEqual(expect.arrayContaining(['appId', 'caseName', 'userId']));
    expect(schema.required).not.toContain('caseId');
    expect(schema.required).not.toContain('docketId');
  });
});
