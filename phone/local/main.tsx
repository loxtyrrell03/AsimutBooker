import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import HomePage from '../app/page';
import '../app/globals.css';

const root = document.getElementById('root');
if (!root) throw new Error('Asimut phone root element is missing');

createRoot(root).render(
  <StrictMode>
    <HomePage />
  </StrictMode>,
);
