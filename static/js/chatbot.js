const chatForm = document.getElementById("chatForm");
const userMessage = document.getElementById("userMessage");
const chatMessages = document.getElementById("chatMessages");

chatForm.addEventListener("submit", async function(event) {
    event.preventDefault();

    const message = userMessage.value.trim();

    if (message === "") return;

    // Display user message
    const userDiv = document.createElement("div");
    userDiv.className = "user-message";
    userDiv.textContent = message;
    chatMessages.appendChild(userDiv);

    userMessage.value = "";

    // Show loading message
    const botDiv = document.createElement("div");
    botDiv.className = "bot-message";
    botDiv.textContent = "RescueBot is thinking...";
    chatMessages.appendChild(botDiv);

    chatMessages.scrollTop = chatMessages.scrollHeight;

    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                message: message
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.reply || "Something went wrong.");
        }

        botDiv.textContent = data.reply;

    } catch (error) {
        botDiv.textContent = "Sorry! I could not connect to the AI. Please try again.";
        console.error(error);
    }

    chatMessages.scrollTop = chatMessages.scrollHeight;
});