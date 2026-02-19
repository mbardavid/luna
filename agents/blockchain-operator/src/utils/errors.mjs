export class OperatorError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'OperatorError';
    this.code = code;
    this.details = details;
  }
}
