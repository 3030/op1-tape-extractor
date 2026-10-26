/**
 * Audio output device listing. Choosing an output needs AudioContext.setSinkId
 * (Chrome, Edge); other browsers always play through the system default.
 */

export interface OutputDevice {
  id: string;
  label: string;
}

export function outputSelectionSupported(): boolean {
  return typeof AudioContext !== 'undefined' && 'setSinkId' in AudioContext.prototype;
}

export interface OutputDeviceList {
  defaultLabel: string;
  devices: OutputDevice[];
  labelsHidden: boolean;
}

export async function listOutputDevices(): Promise<OutputDeviceList> {
  const all = (await navigator.mediaDevices?.enumerateDevices?.()) ?? [];
  const outputs = all.filter((d) => d.kind === 'audiooutput');
  const defaultEntry = outputs.find((d) => d.deviceId === 'default');
  const defaultLabel = defaultEntry?.label.replace(/^Default\s*-\s*/, '') || 'Default';
  const devices = outputs
    .filter((d) => d.deviceId !== 'default' && d.deviceId !== 'communications')
    .map((d, i) => ({ id: d.deviceId, label: d.label || `Output ${i + 1}` }));
  // Browsers hide device names until the page has had microphone permission once
  const labelsHidden = outputs.length > 0 && outputs.every((d) => !d.label);
  return { defaultLabel, devices, labelsHidden };
}

/** Ask for microphone access once so the browser reveals output device names. */
export async function unlockDeviceLabels(): Promise<boolean> {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}
