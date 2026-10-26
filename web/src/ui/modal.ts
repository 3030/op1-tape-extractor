/**
 * Modal dialogs. A modal closes only through its own buttons: never on a
 * backdrop click and never on Escape.
 */

import { h } from './dom';
import { icon } from './icons';

let openCount = 0;

/** True while any modal is open (keyboard shortcuts pause meanwhile) */
export function isModalOpen(): boolean {
  return openCount > 0;
}

interface ModalButton<T> {
  label: string;
  value: T;
  primary?: boolean;
}

function showModal<T>(
  title: string,
  body: Node,
  buttons: ModalButton<T>[],
  closeValue: T,
  extraClass = '',
): Promise<T> {
  return new Promise((resolve) => {
    openCount++;
    const close = (value: T) => {
      openCount--;
      backdrop.remove();
      previousFocus?.focus();
      resolve(value);
    };
    const previousFocus = document.activeElement as HTMLElement | null;

    const closeBtn = h('button', { class: 'modal-close', type: 'button', 'aria-label': 'Close', onclick: () => close(closeValue) }, icon('close', 16));
    const footer = buttons.length
      ? h('div', { class: 'modal-buttons' }, ...buttons.map((b) =>
        h('button', { class: b.primary ? 'btn-accent' : 'btn-plain', type: 'button', onclick: () => close(b.value) }, b.label)))
      : null;
    const dialog = h('div', { class: `modal ${extraClass}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('div', { class: 'modal-header' }, h('h2', { class: 'modal-title' }, title), closeBtn),
      h('div', { class: 'modal-body' }, body),
      footer,
    );
    const backdrop = h('div', { class: 'modal-backdrop' }, dialog);
    // Keep keyboard focus inside the dialog; Escape intentionally does nothing
    backdrop.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
      }
    });
    document.body.append(backdrop);
    const primary = dialog.querySelector<HTMLButtonElement>('.modal-buttons .btn-accent') ?? closeBtn;
    primary.focus();
  });
}

/** Yes/No question. Resolves true only when the confirm button is pressed. */
export function confirmModal(title: string, message: string, confirmLabel: string, cancelLabel = 'Cancel'): Promise<boolean> {
  const body = h('p', { class: 'modal-message' }, message);
  return showModal(title, body, [
    { label: cancelLabel, value: false },
    { label: confirmLabel, value: true, primary: true },
  ], false);
}

/** Information message with a single OK button */
export function infoModal(title: string, message: string): Promise<void> {
  const body = h('p', { class: 'modal-message' }, message);
  return showModal(title, body, [{ label: 'OK', value: undefined, primary: true }], undefined);
}

/** Custom content with only the close button */
export function contentModal(title: string, body: Node, extraClass = ''): Promise<void> {
  return showModal(title, body, [], undefined, extraClass);
}
