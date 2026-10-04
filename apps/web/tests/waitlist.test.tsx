import { fireEvent, render, screen } from "@testing-library/react";
import { Waitlist, WaitlistProvider } from "@/components/landing/Waitlist";

it.each(["hero", "closing"] as const)("confirms a valid %s submission in both waitlist entry points", (source) => {
  render(<WaitlistProvider><Waitlist id="hero" /><Waitlist id="closing" /></WaitlistProvider>);
  const input = screen.getByTestId(`${source}-waitlist-email`);
  fireEvent.change(input, { target: { value: "preview@example.com" } });
  fireEvent.click(screen.getByTestId(`${source}-waitlist-submit`));
  expect(screen.getByTestId(`${source}-waitlist-success`)).toHaveFocus();
  expect(screen.getByTestId("hero-waitlist-success")).toBeInTheDocument();
  expect(screen.getByTestId("closing-waitlist-success")).toBeInTheDocument();
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(screen.queryByTestId("hero-waitlist-email")).toBeNull();
  expect(screen.queryByTestId("closing-waitlist-email")).toBeNull();
});
