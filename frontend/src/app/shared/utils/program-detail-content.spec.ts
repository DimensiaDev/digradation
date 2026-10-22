import { formatProgramHours } from './program-detail-content';

describe('program detail content', () => {
  it.each([
    [1, '1 час'],
    [2, '2 часа'],
    [4, '4 часа'],
    [5, '5 часов'],
    [11, '11 часов'],
    [21, '21 час'],
    [72, '72 часа'],
    [0, '0 часов']
  ])('formats %i hours as %s', (hours, expected) => {
    expect(formatProgramHours(hours)).toBe(expected);
  });
});
