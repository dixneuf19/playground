// Phone page: emoji and comments for the TV.
const show = connectShow("react", () => ({}), {});

const nameInput = document.getElementById("name");
nameInput.value = stored("name");

document.getElementById("emojis").addEventListener("click", (e) => {
  const button = e.target.closest("button");
  if (!button) return;
  show.send({ type: "react", emoji: button.dataset.emoji });
  button.classList.remove("pop");
  void button.offsetWidth; // restart the animation
  button.classList.add("pop");
  navigator.vibrate?.(10);
});

document.getElementById("comment").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = document.getElementById("text");
  store("name", nameInput.value.trim());
  show.send({ type: "comment", name: nameInput.value, text: text.value });
  text.value = "";
});
