const path = require('path');

module.exports = {
  rootDir: __dirname,
  roots: [path.join(__dirname, 'test')],
  testEnvironment: 'node',
  testMatch: ['**/caseflow-unfiled-matter-contract.spec.js'],
  maxWorkers: 1,
};
