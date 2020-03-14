import asyncio
from typing import List, Tuple
from ready_trader_one import BaseAutoTrader, Instrument, Lifespan, Side
import time
import itertools

class AutoTrader(BaseAutoTrader):
    
    def __init__(self, loop: asyncio.AbstractEventLoop):
        """Initialise a new instance of the AutoTrader class."""
        super(AutoTrader, self).__init__(loop)
        
        # History for each instrument - contains a key for average ask/bid prices and a key for the actual history list
        self.op_history = []
        
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0
        self.trade_tick_list = [] # History of trade ticks
        self.predicted_hedge_price = 0
        self.total_fees = 0.0 # Total fees collected
        self.order_ids = itertools.count(1)

        self.active_order_history = {}

        self.base_time = time.time()

        self.previous_sells = [0] * 10

        self.previous_buys = [0] * 10

        self.rat_mode = False

        self.number_of_matches_in_tick = 0
        
    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.
        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        self.on_order_status_message(client_order_id, 0, 0, 0)
        self.op_send_cancel_order(client_order_id)
        
    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.
        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        # Update operation history for past second
        self.update_op_history()

        self.logger.warning("Below is the active order history:")
        self.logger.warning(str(self.active_order_history))
                
        def order_quantity(trader_stance):
            """trader_stance is a boolean: True = passive, False = aggressive"""
            if trader_stance == True:
                volume = int(min(sum(bid_volumes),sum(ask_volumes))/10000 * 0.5 * (self.number_of_matches_in_tick))
                
                if volume == 0:
                    volume = 1

                return volume
            else:
                volume = int((abs(sum(bid_volumes)-sum(ask_volumes))/10000) * 0.5 * (self.number_of_matches_in_tick))
                
                if volume == 0:
                    volume = 1

                return volume
                
        def check_same_price_order(volume, ask_trading_price, bid_trading_price):
            self.logger.warning("Checking for same price")
            
            ask_order_exist = False
            bid_order_exist = False
            # Variables for checking if identical opposite side order exists
            opposite_bid_found = False
            opposite_ask_found = False
            ask_order_num_cancel = 0
            bid_order_num_cancel = 0
            old_ask_order_volume = 0
            old_bid_order_volume = 0

            if len(self.active_order_history) != 0:
                #Checks for identical orders in active order_history
                for key in list(self.active_order_history.keys()):
                    if self.active_order_history[key][2] == 0 and self.active_order_history[key][3] == ask_trading_price:
                        ask_order_num_cancel = key
                        old_ask_order_volume = self.active_order_history[key][4]
                        ask_order_exist = True
                    if self.active_order_history[key][2] == 1 and self.active_order_history[key][3] == ask_trading_price: # If we find a bid of the same price as our ask
                        opposite_bid_found = True
                        

                    if self.active_order_history[key][2] == 1 and self.active_order_history[key][3] == bid_trading_price:
                        bid_order_num_cancel = key
                        old_bid_order_volume = self.active_order_history[key][4]
                        bid_order_exist = True
                    if self.active_order_history[key][2] == 0 and self.active_order_history[key][3] == bid_trading_price: # If we find a ask of the same price as our bid
                        opposite_add_found = True

                self.logger.warning("Looped through active order history to check price")
                
                if not opposite_bid_found:
                    if ask_order_exist == True:
                        self.logger.warning("Found same ask price")
                        if self.op_send_cancel_order(ask_order_num_cancel): # This function returns true if cancel is successful
                            self.ask_id = next(self.order_ids)
                            self.op_send_insert_order(self.ask_id, Side.SELL, ask_trading_price, order_volume + old_ask_order_volume, Lifespan.GOOD_FOR_DAY)
                            self.logger.warning("Replaced ask order with new volume")
                    else:
                        self.ask_id = next(self.order_ids)
                        self.op_send_insert_order(self.ask_id, Side.SELL, ask_trading_price, order_volume, Lifespan.GOOD_FOR_DAY)

                if not opposite_ask_found:
                    if bid_order_exist == True:
                        self.logger.warning("Found same bid price")
                        if self.op_send_cancel_order(bid_order_num_cancel): # This function returns true if cancel is successful
                            self.bid_id = next(self.order_ids)
                            self.op_send_insert_order(self.bid_id, Side.BUY, bid_trading_price, order_volume + old_bid_order_volume, Lifespan.GOOD_FOR_DAY)
                            self.logger.warning("Replaced bid order with new volume")
                    else:
                        self.bid_id = next(self.order_ids)
                        self.op_send_insert_order(self.bid_id, Side.BUY, bid_trading_price, order_volume, Lifespan.GOOD_FOR_DAY)
            else:
                self.ask_id = next(self.order_ids)
                self.op_send_insert_order(self.ask_id, Side.SELL, ask_trading_price, order_volume, Lifespan.GOOD_FOR_DAY)

                self.bid_id = next(self.order_ids)
                self.op_send_insert_order(self.bid_id, Side.BUY, bid_trading_price, order_volume, Lifespan.GOOD_FOR_DAY)

        if len(bid_prices) > 0 and len(ask_prices) > 0:
            if self.rat_mode:
                #self.logger.warning("RAT MODE ACTIVATED")
                # Make an ask at the last trading price + ask_bid_spread
                if self.position >= 25:
                    ask_trading_price = self.round_to_trade_tick(ask_prices[0])
                    self.ask_id = next(self.order_ids)
                    self.op_send_insert_order(self.ask_id, Side.SELL, ask_trading_price, 5, Lifespan.GOOD_FOR_DAY)
                elif self.position <= -25:
                    bid_trading_price = self.round_to_trade_tick(bid_prices[0]) 
                    self.bid_id = next(self.order_ids)
                    self.op_send_insert_order(self.bid_id, Side.BUY, bid_trading_price, 5, Lifespan.GOOD_FOR_DAY)

                if abs(self.position) < 25:
                    self.rat_mode = False
            elif instrument == Instrument.FUTURE:
                self.predicted_hedge_price = (bid_prices[0] + ask_prices[0])/2
            elif instrument == Instrument.ETF and self.predicted_hedge_price > 0:
                for p in bid_prices:
                    if self.predicted_hedge_price > p:
                        self.bid_id = next(self.order_ids)
                        self.op_send_insert_order(self.bid_id, Side.BUY, p, 2, Lifespan.GOOD_FOR_DAY)
                for p in ask_prices:
                    if self.predicted_hedge_price < p:
                        self.ask_id = next(self.order_ids)
                        self.op_send_insert_order(self.ask_id, Side.SELL, p, 2, Lifespan.GOOD_FOR_DAY)
                    
    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int, fees: int) -> None:
        """Called when the status of one of your orders changes.
        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.
        If an order is cancelled its remaining volume will be zero.
        """
        # Update operation history for past second
        self.update_op_history()

        if remaining_volume > 0 and fill_volume > 0 and client_order_id in self.active_order_history.keys():
            temp = list(self.active_order_history[client_order_id]) # Convert tuple to list
            temp[1] = 0
            self.active_order_history[client_order_id] = tuple(temp)
            

        if remaining_volume == 0 and client_order_id in self.active_order_history.keys():
            del self.active_order_history[client_order_id]

        self.total_fees += fees

        self.number_of_matches_in_tick += 1

        self.logger.warning("Total fees: %f", self.total_fees)

        """
        if remaining_volume != 0:
            if self.op_count < 20:
                self.send_amend_order(client_order_id, int(remaining_volume * 1.1))
                #dont know what the third parameter for the above should be. Need concrete position information to implement this properly 
                self.op_count += 1
        """
        
    def on_position_change_message(self, future_position: int, etf_position: int) -> None:
        """Called when your position changes.
        Since every trade in the ETF is automatically hedged in the future,
        future_position and etf_position will always be the inverse of each
        other (i.e. future_position == -1 * etf_position).
        """
        self.logger.warning("Our position is: %d", self.position)
        self.position = etf_position

        if self.position > 75 or self.position < -75:
            self.rat_mode = True
            self.predicted_hedge_price = -1
        
    def on_trade_ticks_message(self, instrument: int, trade_ticks: List[Tuple[int, int]]) -> None:
        """Called periodically to report trading activity on the market.
        Each trade tick is a pair containing a price and the number of lots
        traded at that price since the last trade ticks message.
        """
        self.trade_tick_list.append(trade_ticks)
        self.number_of_matches_in_tick = 0

        for key in list(self.active_order_history.keys()):
            temp = list(self.active_order_history[key]) # Convert tuple to list
            temp[1] += 1
            self.active_order_history[key] = tuple(temp)
            if self.active_order_history[key][1] > 3:
                self.op_send_cancel_order(key)
            
    # Helper functions for checking breaches
    def op_send_insert_order(self, client_order_id: int, side: Side, price: int, volume: int, lifespan: Lifespan) -> None:
        if self.get_projected_op_rate(1) <= 19.5: # Technically should be 20 - setting it stricter for now
            if (side == Side.BUY and self.position < 100) or (side == Side.SELL and self.position > -100):
                # Attempting to correct position limits
                if side == Side.BUY:
                    if self.position + volume >= 100:
                        ask_volume = 90 - self.position
                if side == Side.SELL:
                    if self.position - volume <= -100:
                        ask_volume = -90 - self.position
                        
                self.send_insert_order(client_order_id, side, price, volume, lifespan)
                self.op_history.append(time.time())
                
                # Logging messages
                if side == side.SELL:
                    self.logger.warning("Order num %d selling %d for %d", client_order_id, volume, price)
                if side == side.BUY:
                    self.logger.warning("Order num %d buying %d for %d", client_order_id, volume, price)
                if lifespan == Lifespan.GOOD_FOR_DAY:
                    self.logger.warning("Added to active order history")
                    self.active_order_history[client_order_id] = (client_order_id, 0, int(side), price, volume)
                    

    def op_send_cancel_order(self, client_order_id: int) -> None:
        if self.get_projected_op_rate(2) <=19.5: # Technically should be 20 - setting it stricter for now
            self.send_cancel_order(client_order_id)
            self.logger.warning("Cancelled order: %d", client_order_id)
            #del self.active_order_history[client_order_id]
            self.logger.warning("Deleted order %d from the active_order_list", client_order_id)
            self.op_history.append(time.time())
            return True
            
        return False

    def op_send_amend_order(self, client_order_id: int, volume: int) -> None:
        if self.get_projected_op_rate(1) <= 19.5: # Technically should be 20 - setting it stricter for now
            self.send_amend_order(client_order_id, volume)
            self.op_history.append(time.time())
        
    def update_op_history(self):
        counter = 0
        for entry in self.op_history:
            if time.time() - entry >= 1.1:
                counter += 1
            else:
                break
        for i in range(counter):
            del self.op_history[0]
        
    def get_projected_op_rate(self, num_ops): # Second parameter is the number of ops to be taken
        if len(self.op_history) > 0 and (time.time() - self.op_history[0]) != 0:
            self.logger.warning("Operation limit right now is: %d", (len(self.op_history))/(time.time() - self.op_history[0]))
            self.logger.warning("Projected operation limit is : %d", (len(self.op_history)+num_ops)/(time.time() - self.op_history[0]))
            return (len(self.op_history)+num_ops)/(time.time() - self.op_history[0])
        else: # If list is empty we can probably do a safe insert since op history has the operations from the past second
            return 0

    def round_to_trade_tick(self, integer):
        return int(integer/100) * 100
        
