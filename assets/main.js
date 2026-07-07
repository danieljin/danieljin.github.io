// Keep the footer year current. Vanilla JS — no jQuery.
document.addEventListener('DOMContentLoaded', function () {
  var year = document.getElementById('year');
  if (year) year.textContent = new Date().getFullYear();
});
