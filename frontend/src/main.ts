import "the-new-css-reset/css/reset.css";
import "lituix";
import "./components/app-shell";

const root = document.getElementById("app");
if (root) {
  root.appendChild(document.createElement("app-shell"));
}
