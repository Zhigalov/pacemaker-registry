const fs = require('node:fs');
const models = require('../src/pacemaker_registry/static/rating-models.js');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(({kind, seconds, config}) => models[kind](seconds, config))));
