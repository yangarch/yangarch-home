'use strict';
// All data stays in the static document. No HTML injection or network requests.
const filterButtons = Array.from(document.querySelectorAll('[data-filter]'));
const projects = Array.from(document.querySelectorAll('[data-category]'));
const result = document.getElementById('filter-result');
for (const button of filterButtons) {
  button.addEventListener('click', () => {
    const selected = button.dataset.filter;
    let visible = 0;
    for (const project of projects) {
      project.hidden = selected !== 'all' && project.dataset.category !== selected;
      if (!project.hidden) visible += 1;
    }
    for (const item of filterButtons) {
      item.setAttribute('aria-pressed', String(item === button));
    }
    if (result) result.textContent = `${visible}개의 프로젝트`;
  });
}
