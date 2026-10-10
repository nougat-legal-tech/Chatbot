const Module = require('module');
const fs = require('fs');
const logPath = process.env.AUTH_REQUIRE_TRACE;
const original = Module._load;
Module._load = function(request, parent, isMain) {
  const started = Date.now();
  const parentFile = parent && parent.filename ? parent.filename : '';
  if (parentFile.includes('api\\server\\index.js') || String(request).includes('api\\server\\index.js')) {
    fs.appendFileSync(logPath, `BEGIN ${parentFile} => ${request}\n`);
  }
  try {
    const result = original.apply(this, arguments);
    const elapsed = Date.now() - started;
    if (elapsed > 250 || parentFile.includes('api\\server\\index.js')) {
      fs.appendFileSync(logPath, `${elapsed}ms ${parentFile} => ${request}\n`);
    }
    return result;
  } catch (error) {
    fs.appendFileSync(logPath, `ERROR ${parentFile} => ${request}: ${error && error.stack}\n`);
    throw error;
  }
};
