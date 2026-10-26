import './ui/styles.css';
import { App } from './ui/app';

const app = new App();
document.getElementById('app')!.replaceWith(app.el);
