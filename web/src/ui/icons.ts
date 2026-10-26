/**
 * Semantic icon names -> Phosphor icons (MIT). Only the SVGs imported here ship.
 */

import arrowsClockwise from '@phosphor-icons/core/bold/arrows-clockwise-bold.svg?raw';
import arrowsIn from '@phosphor-icons/core/bold/arrows-in-line-horizontal-bold.svg?raw';
import check from '@phosphor-icons/core/bold/check-bold.svg?raw';
import folderOpen from '@phosphor-icons/core/bold/folder-open-bold.svg?raw';
import magnet from '@phosphor-icons/core/bold/magnet-bold.svg?raw';
import minus from '@phosphor-icons/core/bold/minus-bold.svg?raw';
import plus from '@phosphor-icons/core/bold/plus-bold.svg?raw';
import question from '@phosphor-icons/core/bold/question-bold.svg?raw';
import repeat from '@phosphor-icons/core/bold/repeat-bold.svg?raw';
import scissors from '@phosphor-icons/core/bold/scissors-bold.svg?raw';
import speakerHigh from '@phosphor-icons/core/bold/speaker-high-bold.svg?raw';
import voicemail from '@phosphor-icons/core/bold/voicemail-bold.svg?raw';
import x from '@phosphor-icons/core/bold/x-bold.svg?raw';
import play from '@phosphor-icons/core/fill/play-fill.svg?raw';
import stop from '@phosphor-icons/core/fill/stop-fill.svg?raw';

const ICONS = {
  play,
  stop,
  loop: repeat,
  select: voicemail,
  split: scissors,
  glue: arrowsIn,
  add: plus,
  remove: minus,
  snap: magnet,
  checked: check,
  folder: folderOpen,
  help: question,
  close: x,
  audioOutput: speakerHigh,
  refresh: arrowsClockwise,
} as const;

export type IconName = keyof typeof ICONS;

/** An inline SVG icon element that takes its color from CSS `color` */
export function icon(name: IconName, size = 20): SVGSVGElement {
  const template = document.createElement('template');
  template.innerHTML = ICONS[name].trim();
  const svg = template.content.firstElementChild as SVGSVGElement;
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('icon');
  return svg;
}
