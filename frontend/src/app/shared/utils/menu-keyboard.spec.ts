import { nextMenuIndex } from './menu-keyboard';

describe('nextMenuIndex', () => {
  it('wraps down from the last item to the first', () => {
    expect(nextMenuIndex('ArrowDown', 2, 3)).toBe(0);
  });

  it('wraps up from the first item to the last', () => {
    expect(nextMenuIndex('ArrowUp', 0, 3)).toBe(2);
  });

  it('jumps to the first and last item', () => {
    expect(nextMenuIndex('Home', 2, 3)).toBe(0);
    expect(nextMenuIndex('End', 0, 3)).toBe(2);
  });

  it('ignores other keys and an empty menu', () => {
    expect(nextMenuIndex('Tab', 1, 3)).toBeNull();
    expect(nextMenuIndex('ArrowDown', 0, 0)).toBeNull();
  });
});
