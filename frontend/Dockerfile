FROM node:22-alpine

WORKDIR /frontend

# Copy dependency manifests
COPY package.json package-lock.json* ./

# Install npm dependencies
RUN npm install

# Copy frontend source files
COPY . .

# Expose port 5173 for Vite dev server
EXPOSE 5173

# Run the dev server exposed to all network interfaces
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
