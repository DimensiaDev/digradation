module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    // config-conventional forbids sentence-case subjects ("Tools, tests, ..."),
    // which is a style this project uses. Case is not checked; the rest of the
    // conventional rules stay on.
    'subject-case': [0],
  },
};
