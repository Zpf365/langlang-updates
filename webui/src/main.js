import { createApp } from "vue";
import App from "./App.vue";
import router from "./router";
import "./styles.css";
import { markUiReady } from "./updateReady";

createApp(App).use(router).mount("#app");
markUiReady();
